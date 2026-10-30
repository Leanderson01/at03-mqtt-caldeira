"""
CAT – Compute Average Temperature.

Assina caldeira/sensores/+/temperatura ('+' casa com qualquer id de sensor,
então sensores podem entrar e sair sem mudar nada aqui). A cada leitura:
  1. guarda a leitura numa janela deslizante de --janela segundos (padrão 120)
     e descarta as leituras mais antigas;
  2. calcula a média da janela e publica em caldeira/media
     (cada sensor pesa igual: média das médias de cada sensor na janela —
     ver a explicação em calcular_media);
  3. se média atual − média anterior >= 5        -> caldeira/alertas/aumento_repentino
     se média atual > 200 (ao entrar nessa faixa) -> caldeira/alertas/temperatura_alta

Por padrão só um AUMENTO de 5 °C dispara o alerta (é o nome do evento no
enunciado); --variacao-absoluta passa a alertar também quedas (|Δ| >= 5).
A faixa alta tem histerese: depois de alertar, a média precisa cair abaixo
de 200 − --histerese para o próximo cruzamento alertar de novo. Sem isso, a
oscilação natural da janela perto de 200 °C geraria um alerta atrás do outro.

O CAT é a "calculadora remota" do cenário: ninguém o chama diretamente;
ele reage ao que é publicado no broker.
"""

import argparse  # lê os parâmetros da linha de comando
import json  # lê e monta payloads JSON
import time  # relógio usado na janela deslizante

import paho.mqtt.client as mqtt  # cliente MQTT da Eclipse Paho

TOPICO_LEITURAS = "caldeira/sensores/+/temperatura"  # assinatura com curinga de um nível: todos os sensores
TOPICO_MEDIA = "caldeira/media"  # onde a média é publicada
TOPICO_AUMENTO = "caldeira/alertas/aumento_repentino"  # alerta de variação brusca
TOPICO_ALTA = "caldeira/alertas/temperatura_alta"  # alerta de temperatura alta
QOS_LEITURA = 0  # leituras: frequentes, perder uma isolada não muda a média; QoS 0 evita o custo do PUBACK
QOS_MEDIA = 0  # média: uma nova chega a cada leitura, então a perda de uma é logo corrigida
QOS_ALERTA = 1  # alertas: não podem se perder; QoS 1 ("pelo menos uma vez") reenvia até receber o PUBACK

LIMIAR_AUMENTO = 5.0  # diferença entre médias consecutivas que caracteriza aumento repentino (°C)
LIMIAR_ALTA = 200.0  # média acima disso é temperatura alta (°C)


class CAT:  # estado do serviço: janela de leituras e últimas decisões
    def __init__(self, client, janela, lembrete, histerese, variacao_absoluta):  # configura o serviço
        self.client = client  # cliente MQTT usado para publicar
        self.janela = janela  # tamanho da janela deslizante, em segundos
        self.lembrete = lembrete  # repetir o alerta de alta a cada N s enquanto durar (0 = nunca)
        self.histerese = histerese  # quanto a média precisa cair abaixo de 200 para sair da faixa alta
        self.variacao_absoluta = variacao_absoluta  # se True, quedas de 5 °C também alertam
        self.leituras = []  # lista de (timestamp, temperatura, sensor_id) dentro da janela
        self.media_anterior = None  # última média calculada (None antes da primeira)
        self.em_alta = False  # se a média está atualmente acima de 200 °C
        self.ultimo_alerta_alta = 0.0  # quando o alerta de alta foi publicado pela última vez

    def nova_leitura(self, sensor_id, temperatura, timestamp):  # processa uma leitura recebida
        agora = time.time()  # instante atual
        limite = agora - self.janela  # leituras com timestamp anterior a isso estão fora da janela
        if timestamp < limite:  # a leitura já chegou velha
            print(f"  leitura antiga de {sensor_id} ignorada")  # registra e descarta
            return  # nada a recalcular
        self.leituras.append((timestamp, temperatura, sensor_id))  # entra na janela
        self.leituras = [l for l in self.leituras if l[0] >= limite]  # descarta as mais antigas que a janela

        media, sensores = self.calcular_media()  # média da janela e quantos sensores participaram
        print(f"leitura {sensor_id}={temperatura:.1f} °C -> média {media:.2f} °C ({len(self.leituras)} leituras, {sensores} sensores)")  # log da média
        self.publicar(TOPICO_MEDIA, {"media": round(media, 2), "qtd_leituras": len(self.leituras), "timestamp": agora}, QOS_MEDIA)  # publica a média

        if self.media_anterior is not None:  # a primeira média não tem com quem comparar
            variacao = media - self.media_anterior  # diferença entre as duas últimas médias
            if variacao >= LIMIAR_AUMENTO or (self.variacao_absoluta and abs(variacao) >= LIMIAR_AUMENTO):  # regra do aumento repentino
                self.alertar(TOPICO_AUMENTO, "aumento_repentino", media, agora)  # dispara o alerta

        if media > LIMIAR_ALTA:  # regra da temperatura alta
            entrou_agora = not self.em_alta  # só alerta quando o estado muda...
            lembrar = self.lembrete > 0 and agora - self.ultimo_alerta_alta >= self.lembrete  # ...ou quando vence o lembrete
            if entrou_agora or lembrar:  # evita alertar a cada leitura
                self.alertar(TOPICO_ALTA, "temperatura_alta", media, agora)  # dispara o alerta
                self.ultimo_alerta_alta = agora  # marca o horário do alerta
            self.em_alta = True  # agora está na faixa alta
        elif self.em_alta and media < LIMIAR_ALTA - self.histerese:  # estava alta e caiu abaixo de 200 − histerese
            print(f"  média voltou ao normal ({media:.2f} °C)")  # registra a saída da faixa alta
            self.em_alta = False  # o próximo cruzamento vai alertar de novo

        self.media_anterior = media  # guarda para comparar com a próxima

    def calcular_media(self):  # média da janela com o mesmo peso para cada sensor
        # Com leituras periódicas numa janela de tempo, cada sensor tem ora k,
        # ora k+1 leituras dentro dela (uma sai antes da próxima chegar). Numa
        # média simples, o peso de um sensor quente oscila junto e a média
        # "serrilha" vários graus a cada leitura, disparando aumento repentino
        # sem a temperatura ter mudado. Tirando a média de cada sensor antes,
        # todo sensor pesa 1/N e a média só muda quando a temperatura muda.
        por_sensor = {}  # sensor_id -> lista de temperaturas na janela
        for _ts, temperatura, sensor_id in self.leituras:  # agrupa as leituras por sensor
            por_sensor.setdefault(sensor_id, []).append(temperatura)  # acrescenta na lista do sensor
        medias = [sum(v) / len(v) for v in por_sensor.values()]  # média de cada sensor
        return sum(medias) / len(medias), len(medias)  # média das médias e número de sensores

    def alertar(self, topico, tipo, media, agora):  # publica um alerta
        payload = {  # payload do alerta
            "tipo": tipo,  # aumento_repentino ou temperatura_alta
            "media_atual": round(media, 2),  # média que disparou o alerta
            "media_anterior": None if self.media_anterior is None else round(self.media_anterior, 2),  # média anterior
            "timestamp": agora,  # instante do alerta
        }  # fim do payload
        print(f"  *** ALERTA {tipo}: {payload['media_anterior']} -> {payload['media_atual']} °C")  # log do alerta
        self.publicar(topico, payload, QOS_ALERTA)  # publica com QoS 1

    def publicar(self, topico, payload, qos):  # publica um dicionário como JSON
        self.client.publish(topico, json.dumps(payload), qos=qos)  # envia o PUBLISH ao broker


def ao_conectar(client, userdata, flags, reason_code, properties):  # callback do CONNACK (API VERSION2)
    if reason_code.is_failure:  # o broker recusou a conexão
        print(f"Conexão recusada: {reason_code}")  # mostra o motivo
        return  # não assina nada
    print(f"Conectado ao broker; assinando {TOPICO_LEITURAS}")  # confirma
    client.subscribe(TOPICO_LEITURAS, qos=QOS_LEITURA)  # SUBSCRIBE aqui (e não no main) para reassinar após cada reconexão


def ao_desconectar(client, userdata, flags, reason_code, properties):  # callback de desconexão (API VERSION2)
    print(f"Desconectado ({reason_code}); o paho vai tentar reconectar")  # a reconexão é automática


def ao_receber(client, userdata, msg):  # callback de cada PUBLISH recebido
    try:  # o payload pode vir mal formado
        dados = json.loads(msg.payload)  # converte o JSON em dicionário
        temperatura = float(dados["temperatura"])  # valor medido
        timestamp = float(dados.get("timestamp", time.time()))  # instante da medição (ou agora, se faltar)
        sensor_id = str(dados.get("sensor_id", msg.topic.split("/")[2]))  # id do sensor (ou o pedaço do tópico)
    except (ValueError, KeyError, TypeError, IndexError):  # JSON inválido ou campos faltando
        print(f"payload inválido em {msg.topic}: {msg.payload!r}")  # registra e ignora
        return  # não derruba o serviço
    userdata.nova_leitura(sensor_id, temperatura, timestamp)  # entrega ao CAT, guardado em userdata


def main():  # ponto de entrada do serviço
    parser = argparse.ArgumentParser(description="CAT - Compute Average Temperature (MQTT)")  # define os argumentos
    parser.add_argument("--janela", type=float, default=120, help="janela deslizante em segundos (padrão 120)")  # tamanho da janela
    parser.add_argument("--lembrete", type=float, default=0, help="repete o alerta de alta a cada N s enquanto durar (0 = não repete)")  # anti-spam configurável
    parser.add_argument("--histerese", type=float, default=5, help="°C abaixo de 200 para sair da faixa alta (padrão 5)")  # evita alertas repetidos perto de 200
    parser.add_argument("--variacao-absoluta", action="store_true", help="alerta também quedas de 5 °C (|Δ| >= 5)")  # regra alternativa
    parser.add_argument("--broker", default="localhost", help="host do broker MQTT")  # onde está o Mosquitto
    parser.add_argument("--porta", type=int, default=1883, help="porta do broker (padrão 1883)")  # porta padrão do MQTT
    args = parser.parse_args()  # lê os argumentos digitados

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="cat")  # cria o cliente com client_id único "cat"
    client.user_data_set(CAT(client, args.janela, args.lembrete, args.histerese, args.variacao_absoluta))  # o estado do CAT vai como userdata para os callbacks
    client.on_connect = ao_conectar  # registra o callback de conexão
    client.on_disconnect = ao_desconectar  # registra o callback de desconexão
    client.on_message = ao_receber  # registra o callback de mensagem recebida
    client.reconnect_delay_set(min_delay=1, max_delay=30)  # reconexão automática: espera de 1 s a 30 s entre tentativas
    client.connect_async(args.broker, args.porta, keepalive=60)  # agenda a conexão (CONNECT); não falha se o broker ainda não subiu
    print(f"CAT iniciado: janela={args.janela}s, broker={args.broker}:{args.porta} (Ctrl+C para sair)")  # resumo da configuração
    try:  # Ctrl+C encerra
        client.loop_forever(retry_first_connection=True)  # roda a rede do paho nesta thread, reconectando sempre que cair
    except KeyboardInterrupt:  # o usuário apertou Ctrl+C
        print("\nEncerrando CAT.")  # mensagem de saída
        client.disconnect()  # envia DISCONNECT ao broker


if __name__ == "__main__":  # só executa main() quando o arquivo é rodado diretamente
    main()  # inicia o serviço
