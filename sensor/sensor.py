"""
Sensor de temperatura simulado da caldeira.

Cada execução deste arquivo é UM sensor (um processo independente, com
client_id próprio). Para aumentar ou diminuir o número de sensores, basta
abrir ou fechar processos — o CAT não precisa saber quantos existem.

Publica em caldeira/sensores/{id}/temperatura o payload:
    {"sensor_id": "s1", "temperatura": 183.4, "timestamp": 1730300000.0}

Modos:
  normal  oscila em torno de --base com ruído de ±2 °C
  pico    normal por --leituras-antes leituras e depois salta --salto °C
          (dispara o alerta de aumento repentino no CAT)
  alta    sobe --passo °C a cada leitura até --teto
          (leva a média acima de 200 °C e dispara o alerta de temperatura alta)
"""

import argparse  # lê os parâmetros do sensor da linha de comando
import json  # monta o payload JSON
import random  # gera o ruído das leituras
import time  # timestamp e intervalo entre leituras

import paho.mqtt.client as mqtt  # cliente MQTT da Eclipse Paho

QOS_LEITURA = 0  # QoS 0 ("no máximo uma vez"): leituras chegam a cada intervalo, perder uma isolada não muda a média


class Simulador:  # gera a sequência de temperaturas conforme o modo
    def __init__(self, args):  # guarda a configuração
        self.args = args  # parâmetros da linha de comando
        self.leituras = 0  # quantas leituras já foram geradas
        self.atual = args.base  # temperatura de partida no modo alta

    def proxima(self):  # devolve a próxima temperatura
        self.leituras += 1  # conta a leitura
        ruido = random.uniform(-2, 2)  # ruído de ±2 °C presente em todos os modos
        if self.args.modo == "pico" and self.leituras > self.args.leituras_antes:  # depois das leituras normais, o pico
            return round(self.args.base + self.args.salto + ruido, 1)  # patamar alto e repentino
        if self.args.modo == "alta":  # aquecimento gradual
            self.atual = min(self.atual + self.args.passo, self.args.teto)  # sobe um passo, até o teto
            return round(self.atual + ruido, 1)  # leitura com ruído
        return round(self.args.base + ruido, 1)  # modo normal (ou pico antes do salto)


def ao_conectar(client, userdata, flags, reason_code, properties):  # callback do CONNACK (API VERSION2)
    if reason_code.is_failure:  # o broker recusou a conexão
        print(f"[{userdata}] Conexão recusada: {reason_code}")  # mostra o motivo
    else:  # conexão aceita
        print(f"[{userdata}] Conectado ao broker")  # confirma


def ao_desconectar(client, userdata, flags, reason_code, properties):  # callback de desconexão (API VERSION2)
    print(f"[{userdata}] Desconectado ({reason_code}); o paho vai tentar reconectar")  # a reconexão é automática


def main():  # ponto de entrada do sensor
    parser = argparse.ArgumentParser(description="Sensor de temperatura simulado (MQTT)")  # define os argumentos
    parser.add_argument("--id", default="s1", help="identificador do sensor (padrão s1)")  # vai no tópico e no client_id
    parser.add_argument("--intervalo", type=float, default=60, help="segundos entre leituras (padrão 60; use 5 na demonstração)")  # período de publicação
    parser.add_argument("--base", type=float, default=180, help="temperatura base em °C (padrão 180)")  # valor central
    parser.add_argument("--modo", choices=["normal", "pico", "alta"], default="normal", help="comportamento do sensor")  # cenário simulado
    parser.add_argument("--salto", type=float, default=60, help="modo pico: quanto a temperatura salta, em °C (padrão 60)")  # tamanho do pico
    parser.add_argument("--leituras-antes", type=int, default=3, help="modo pico: leituras normais antes do salto (padrão 3)")  # atraso do pico
    parser.add_argument("--passo", type=float, default=10, help="modo alta: aumento por leitura, em °C (padrão 10)")  # velocidade do aquecimento
    parser.add_argument("--teto", type=float, default=300, help="modo alta: temperatura máxima, em °C (padrão 300)")  # limite do aquecimento
    parser.add_argument("--broker", default="localhost", help="host do broker MQTT")  # onde está o Mosquitto
    parser.add_argument("--porta", type=int, default=1883, help="porta do broker (padrão 1883)")  # porta padrão do MQTT
    args = parser.parse_args()  # lê os argumentos digitados

    client_id = f"sensor-{args.id}"  # client_id único por processo; o broker derruba conexões com id repetido
    topico = f"caldeira/sensores/{args.id}/temperatura"  # tópico onde este sensor publica

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=client_id, userdata=client_id)  # cria o cliente com a API de callbacks v2
    client.on_connect = ao_conectar  # registra o callback de conexão
    client.on_disconnect = ao_desconectar  # registra o callback de desconexão
    client.reconnect_delay_set(min_delay=1, max_delay=30)  # reconexão automática: espera de 1 s a 30 s entre tentativas
    client.connect_async(args.broker, args.porta, keepalive=60)  # agenda a conexão (CONNECT); não falha se o broker ainda não subiu
    client.loop_start()  # inicia a thread de rede do paho (envia, recebe, faz ping e reconecta)

    simulador = Simulador(args)  # gerador de temperaturas
    print(f"[{client_id}] modo={args.modo}, intervalo={args.intervalo}s, tópico={topico}")  # resumo da configuração
    try:  # Ctrl+C encerra
        while True:  # publica indefinidamente
            payload = {  # monta o payload da leitura
                "sensor_id": args.id,  # quem mediu
                "temperatura": simulador.proxima(),  # valor simulado em °C
                "timestamp": time.time(),  # instante da medição (segundos desde 1970)
            }  # fim do payload
            info = client.publish(topico, json.dumps(payload), qos=QOS_LEITURA)  # envia o PUBLISH ao broker
            if info.rc == mqtt.MQTT_ERR_SUCCESS:  # entregue à camada de rede
                print(f"[{client_id}] publicou {payload['temperatura']:.1f} °C")  # registra a leitura
            else:  # sem conexão no momento
                print(f"[{client_id}] sem conexão; leitura {payload['temperatura']:.1f} °C descartada (QoS 0)")  # avisa a perda
            time.sleep(args.intervalo)  # espera até a próxima leitura
    except KeyboardInterrupt:  # o usuário apertou Ctrl+C
        print(f"\n[{client_id}] encerrando")  # mensagem de saída
    finally:  # sempre executa
        client.disconnect()  # envia DISCONNECT ao broker
        client.loop_stop()  # para a thread de rede


if __name__ == "__main__":  # só executa main() quando o arquivo é rodado diretamente
    main()  # inicia o sensor
