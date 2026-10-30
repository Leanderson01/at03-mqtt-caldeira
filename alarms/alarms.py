"""
Alarms – painel de alarmes da caldeira (Tkinter).

Assina:
  caldeira/media        -> mostra a média atual em destaque, com cor da faixa
  caldeira/alertas/#    -> '#' casa com todos os subtópicos de alertas:
                           aumento_repentino (laranja) e temperatura_alta (vermelho)

Thread-safety: os callbacks do paho rodam na thread de rede, e o Tkinter
só pode ser mexido pela thread principal. Por isso os callbacks apenas
colocam eventos numa queue.Queue, e a thread do Tkinter consome a fila a
cada 100 ms com root.after().

Use --console para rodar sem interface gráfica (só imprime no console).
"""

import argparse  # lê os parâmetros da linha de comando
import json  # lê os payloads JSON
import queue  # fila thread-safe entre a thread do paho e a do Tkinter
import time  # formata horários

import paho.mqtt.client as mqtt  # cliente MQTT da Eclipse Paho

TOPICO_MEDIA = "caldeira/media"  # média publicada pelo CAT
TOPICO_ALERTAS = "caldeira/alertas/#"  # curinga multinível: todos os alertas
QOS_MEDIA = 0  # a média chega a cada leitura; perder uma não importa
QOS_ALERTAS = 1  # alertas não podem se perder: QoS 1 na assinatura garante entrega "pelo menos uma vez"

NOMES = {"aumento_repentino": "Aumento repentino", "temperatura_alta": "Temperatura alta"}  # rótulos legíveis
CORES_ALERTA = {"aumento_repentino": "#e67e22", "temperatura_alta": "#c0392b"}  # laranja e vermelho
FAIXAS = [  # faixas da média: (limite superior, nome, cor de fundo)
    (190, "NORMAL", "#27ae60"),  # até 190 °C: verde
    (200, "ATENÇÃO", "#d4a017"),  # de 190 a 200 °C: amarelo
    (float("inf"), "CRÍTICA", "#c0392b"),  # acima de 200 °C: vermelho
]  # fim das faixas


def faixa(media):  # descobre a faixa de uma média
    for limite, nome, cor in FAIXAS:  # percorre as faixas em ordem
        if media <= limite:  # primeira faixa que comporta o valor
            return nome, cor  # devolve nome e cor
    return FAIXAS[-1][1], FAIXAS[-1][2]  # só por segurança (inf cobre tudo)


def hora(ts):  # converte timestamp em "HH:MM:SS"
    return time.strftime("%H:%M:%S", time.localtime(ts))  # horário local


def texto_alerta(alerta):  # linha de console de um alerta
    anterior = alerta.get("media_anterior")  # média anterior (pode ser None)
    anterior = "—" if anterior is None else f"{anterior:.2f}"  # formata
    return f"[{hora(alerta['timestamp'])}] ALARME {NOMES.get(alerta['tipo'], alerta['tipo'])}: {anterior} -> {alerta['media_atual']:.2f} °C"  # texto final


# ---------------------------------------------------------------------------
# Lado MQTT: roda na thread de rede do paho e só fala com a fila
# ---------------------------------------------------------------------------

def ao_conectar(client, userdata, flags, reason_code, properties):  # callback do CONNACK (API VERSION2)
    if reason_code.is_failure:  # o broker recusou a conexão
        userdata.put(("status", f"Conexão recusada: {reason_code}"))  # avisa a interface
        return  # não assina nada
    client.subscribe([(TOPICO_MEDIA, QOS_MEDIA), (TOPICO_ALERTAS, QOS_ALERTAS)])  # um SUBSCRIBE com os dois filtros; refeito a cada reconexão
    print("Conectado ao broker; assinando caldeira/media e caldeira/alertas/#")  # registra no console
    userdata.put(("status", "Conectado ao broker"))  # avisa a interface


def ao_desconectar(client, userdata, flags, reason_code, properties):  # callback de desconexão (API VERSION2)
    print(f"Desconectado ({reason_code}); o paho vai tentar reconectar")  # registra no console
    userdata.put(("status", f"Desconectado ({reason_code}) – reconectando..."))  # a reconexão é automática


def ao_receber(client, userdata, msg):  # callback de cada PUBLISH recebido
    try:  # o payload pode vir mal formado
        dados = json.loads(msg.payload)  # converte o JSON em dicionário
    except ValueError:  # não é JSON
        print(f"payload inválido em {msg.topic}: {msg.payload!r}")  # registra e ignora
        return  # não derruba o serviço
    if msg.topic == TOPICO_MEDIA:  # chegou uma média
        userdata.put(("media", dados))  # entrega à interface pela fila
    else:  # chegou um alerta (caldeira/alertas/...)
        dados.setdefault("tipo", msg.topic.rsplit("/", 1)[-1])  # se faltar o tipo, usa o fim do tópico
        dados.setdefault("timestamp", time.time())  # se faltar o horário, usa agora
        print(texto_alerta(dados))  # o alarme sempre aparece também no console
        userdata.put(("alerta", dados))  # entrega à interface pela fila


def criar_cliente(fila, args):  # monta e conecta o cliente MQTT
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="alarms", userdata=fila)  # client_id único "alarms"; a fila vai como userdata
    client.on_connect = ao_conectar  # registra o callback de conexão
    client.on_disconnect = ao_desconectar  # registra o callback de desconexão
    client.on_message = ao_receber  # registra o callback de mensagem
    client.reconnect_delay_set(min_delay=1, max_delay=30)  # reconexão automática: espera de 1 s a 30 s entre tentativas
    client.connect_async(args.broker, args.porta, keepalive=60)  # agenda a conexão (CONNECT); não falha se o broker ainda não subiu
    return client  # devolve o cliente pronto


# ---------------------------------------------------------------------------
# Lado interface: roda só na thread principal
# ---------------------------------------------------------------------------

class Painel:  # janela do sistema de monitoramento
    def __init__(self, root, fila):  # monta a interface
        import tkinter as tk  # importado aqui para o modo --console funcionar sem Tkinter instalado
        from tkinter import ttk  # widgets temáticos (Treeview)

        self.tk = tk  # guarda o módulo para usar nos outros métodos
        self.root = root  # janela principal
        self.fila = fila  # fila alimentada pelos callbacks do paho
        self.piscando = 0  # quantas trocas de cor ainda faltam no efeito de piscar
        root.title("Alarms – Caldeira")  # título da janela
        root.geometry("720x520")  # tamanho inicial
        root.configure(bg="#1e1e1e")  # fundo escuro

        self.cabecalho = tk.Label(root, text="MONITORAMENTO DA CALDEIRA", font=("Helvetica", 14, "bold"), fg="white", bg="#1e1e1e")  # título no topo
        self.cabecalho.pack(fill="x", pady=(10, 4))  # posiciona o título

        self.caixa_media = tk.Frame(root, bg="#555555")  # caixa colorida da média
        self.caixa_media.pack(fill="x", padx=16, pady=6)  # posiciona a caixa
        self.rotulo_media = tk.Label(self.caixa_media, text="-- °C", font=("Helvetica", 44, "bold"), fg="white", bg="#555555")  # média em destaque
        self.rotulo_media.pack(pady=(10, 0))  # posiciona a média
        self.rotulo_faixa = tk.Label(self.caixa_media, text="aguardando dados do CAT", font=("Helvetica", 12, "bold"), fg="white", bg="#555555")  # nome da faixa
        self.rotulo_faixa.pack(pady=(0, 10))  # posiciona a faixa

        self.status = tk.Label(root, text="Conectando ao broker...", fg="#bbbbbb", bg="#1e1e1e", anchor="w")  # linha de status da conexão
        self.status.pack(fill="x", padx=16)  # posiciona o status

        tk.Label(root, text="Alarmes", font=("Helvetica", 12, "bold"), fg="white", bg="#1e1e1e", anchor="w").pack(fill="x", padx=16, pady=(10, 2))  # título da lista
        colunas = ("hora", "tipo", "anterior", "atual")  # colunas da lista de alarmes
        self.lista = ttk.Treeview(root, columns=colunas, show="headings", height=10)  # tabela de alarmes
        for coluna, titulo, largura in zip(colunas, ("Horário", "Tipo", "Média anterior", "Média atual"), (90, 200, 140, 140)):  # configura cada coluna
            self.lista.heading(coluna, text=titulo)  # título da coluna
            self.lista.column(coluna, width=largura, anchor="center")  # largura e alinhamento
        for tipo, cor in CORES_ALERTA.items():  # uma "tag" de cor por tipo de alarme
            self.lista.tag_configure(tipo, background=cor, foreground="white")  # vermelho ou laranja
        self.lista.pack(fill="both", expand=True, padx=16, pady=(0, 16))  # posiciona a lista

        self.root.after(100, self.consumir_fila)  # agenda a primeira leitura da fila

    def consumir_fila(self):  # roda na thread do Tkinter a cada 100 ms
        try:  # esvazia a fila sem bloquear
            while True:  # processa todos os eventos pendentes
                tipo, dados = self.fila.get_nowait()  # pega o próximo evento ou lança queue.Empty
                if tipo == "media":  # nova média
                    self.mostrar_media(dados)  # atualiza o destaque
                elif tipo == "alerta":  # novo alarme
                    self.mostrar_alerta(dados)  # adiciona na lista
                else:  # mudança de conexão
                    self.status.config(text=dados)  # atualiza a linha de status
        except queue.Empty:  # a fila acabou
            pass  # nada mais a fazer agora
        self.root.after(100, self.consumir_fila)  # agenda a próxima leitura

    def mostrar_media(self, dados):  # atualiza a média em destaque
        media = float(dados["media"])  # valor da média
        nome, cor = faixa(media)  # faixa e cor correspondentes
        for widget in (self.caixa_media, self.rotulo_media, self.rotulo_faixa):  # pinta a caixa inteira
            widget.config(bg=cor)  # aplica a cor da faixa
        self.rotulo_media.config(text=f"{media:.2f} °C")  # mostra o valor
        self.rotulo_faixa.config(text=f"{nome}  •  {dados.get('qtd_leituras', '?')} leituras na janela")  # mostra faixa e quantidade

    def mostrar_alerta(self, alerta):  # adiciona um alarme na lista
        anterior = alerta.get("media_anterior")  # média anterior (pode ser None)
        valores = (  # valores das colunas
            hora(alerta["timestamp"]),  # horário
            NOMES.get(alerta["tipo"], alerta["tipo"]),  # tipo legível
            "—" if anterior is None else f"{anterior:.2f} °C",  # média anterior
            f"{alerta['media_atual']:.2f} °C",  # média atual
        )  # fim dos valores
        self.lista.insert("", 0, values=valores, tags=(alerta["tipo"],))  # insere no topo, com a cor do tipo
        self.root.bell()  # beep do sistema
        self.piscando = 6  # pisca o título 3 vezes (6 trocas de cor)
        self.piscar(CORES_ALERTA.get(alerta["tipo"], "#c0392b"))  # inicia o efeito

    def piscar(self, cor):  # alterna a cor do título para chamar atenção
        if self.piscando <= 0:  # acabou o efeito
            self.cabecalho.config(bg="#1e1e1e")  # volta ao normal
            return  # fim
        self.cabecalho.config(bg=cor if self.piscando % 2 == 0 else "#1e1e1e")  # alterna entre a cor do alarme e o fundo
        self.piscando -= 1  # conta a troca
        self.root.after(250, self.piscar, cor)  # próxima troca em 250 ms


def main():  # ponto de entrada do serviço
    parser = argparse.ArgumentParser(description="Alarms - painel de alarmes da caldeira (MQTT)")  # define os argumentos
    parser.add_argument("--broker", default="localhost", help="host do broker MQTT")  # onde está o Mosquitto
    parser.add_argument("--porta", type=int, default=1883, help="porta do broker (padrão 1883)")  # porta padrão do MQTT
    parser.add_argument("--console", action="store_true", help="sem interface gráfica, só console")  # modo texto
    args = parser.parse_args()  # lê os argumentos digitados

    fila = queue.Queue()  # canal thread-safe entre o paho e a interface
    client = criar_cliente(fila, args)  # cria o cliente MQTT
    print(f"Alarms conectando a {args.broker}:{args.porta} (Ctrl+C para sair)")  # avisa que iniciou

    if args.console:  # modo texto: os alarmes já são impressos pelo callback
        try:  # Ctrl+C encerra
            client.loop_forever(retry_first_connection=True)  # roda a rede do paho nesta thread, reconectando sempre que cair
        except KeyboardInterrupt:  # o usuário apertou Ctrl+C
            client.disconnect()  # envia DISCONNECT ao broker
        return  # fim do modo console

    import tkinter as tk  # interface gráfica da biblioteca padrão

    client.loop_start()  # a rede do paho roda numa thread separada; a principal fica com o Tkinter
    root = tk.Tk()  # cria a janela principal
    Painel(root, fila)  # monta o painel
    try:  # Ctrl+C no terminal também fecha
        root.mainloop()  # laço de eventos do Tkinter (bloqueia até fechar a janela)
    except KeyboardInterrupt:  # o usuário apertou Ctrl+C
        pass  # segue para o encerramento
    client.disconnect()  # envia DISCONNECT ao broker
    client.loop_stop()  # para a thread de rede


if __name__ == "__main__":  # só executa main() quando o arquivo é rodado diretamente
    main()  # inicia o serviço
