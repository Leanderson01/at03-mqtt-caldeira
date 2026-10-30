# AT03 – Monitoramento de caldeira com MQTT

Trabalho AT03 de Sistemas Distribuídos (SMD/UFC, 2026). Sensores de temperatura simulados publicam leituras num broker **MQTT (Mosquitto)**. O serviço **CAT** (*Compute Average Temperature*) calcula a média dos últimos 120 segundos e publica alertas, e o serviço **Alarms** mostra a média e os alarmes numa interface gráfica (Tkinter).

> 🎥 **Vídeo da demonstração:** [assistir no Loom](https://www.loom.com/share/56ed2cadfec64612b3ce97212cbc314b)

## Paradigma

**Publish-subscribe**, uma forma de comunicação indireta. Nenhum processo conhece os outros: todos falam só com o broker, por tópicos.

```
 sensor s1 ─┐                                   ┌─> CAT ──(média, alertas)──┐
 sensor s2 ─┼─> caldeira/sensores/{id}/temp. ──>│                           │
 sensor sN ─┘          (QoS 0)          BROKER  │                           v
                                      Mosquitto └<── caldeira/media (QoS 0) ┤
                                       :1883     <── caldeira/alertas/... ──┘
                                                      (QoS 1)
                                                │
                                                └─> Alarms (GUI)
```

- **Desacoplamento espacial:** o sensor não sabe que o CAT existe, e o CAT não sabe quantos sensores existem. A assinatura `caldeira/sensores/+/temperatura` aceita qualquer id. Sensores entram e saem sem mudar nada no CAT.
- **Desacoplamento temporal (parcial):** o broker intermedia a entrega, mas uma mensagem QoS 0 publicada sem nenhum assinante conectado se perde. Persistir mensagens exigiria sessões persistentes (`clean_session=False`) ou mensagens *retained*.

| Tópico | Publica | Assina | QoS |
|---|---|---|---|
| `caldeira/sensores/{id}/temperatura` | sensor | CAT (`caldeira/sensores/+/temperatura`) | 0 |
| `caldeira/media` | CAT | Alarms | 0 |
| `caldeira/alertas/aumento_repentino` | CAT | Alarms (`caldeira/alertas/#`) | 1 |
| `caldeira/alertas/temperatura_alta` | CAT | Alarms (`caldeira/alertas/#`) | 1 |

**Por que esses QoS?** As leituras e as médias chegam a cada poucos segundos, e perder uma isolada não muda o resultado. QoS 0 evita a troca extra de pacotes. Um alerta não pode se perder, por isso usa QoS 1 ("pelo menos uma vez"): o broker confirma com PUBACK e o cliente reenvia se a confirmação não chegar.

## Os três projetos

O repositório tem três projetos independentes, seguindo a sugestão do professor. Cada um tem o próprio `requirements.txt` e nenhum importa código dos outros.

### `sensor/` – sensor simulado

Cada processo é **um** sensor (`client_id` = `sensor-<id>`). Payload: `{"sensor_id": "s1", "temperatura": 183.4, "timestamp": 1730300000.0}`.

| Argumento | Padrão | |
|---|---|---|
| `--id` | `s1` | id do sensor (vai no tópico) |
| `--intervalo` | **60** s | período entre leituras (na demonstração: 5 s) |
| `--base` | 180 °C | temperatura base |
| `--modo` | `normal` | `normal`, `pico` ou `alta` |
| `--salto` | 60 °C | modo `pico`: tamanho do salto |
| `--leituras-antes` | 3 | modo `pico`: leituras normais antes do salto |
| `--passo` / `--teto` | 10 / 300 °C | modo `alta`: aquecimento por leitura e limite |

- `normal`: oscila ±2 °C em torno da base.
- `pico`: começa normal e, depois de 3 leituras, salta para base + 60 °C e fica lá. Dispara o **aumento repentino**.
- `alta`: sobe 10 °C por leitura até 300 °C. Leva a média acima de 200 °C e dispara a **temperatura alta**.

`iniciar_sensores.sh N modo intervalo` (Linux/macOS) e `iniciar_sensores.bat N modo intervalo` (Windows) sobem N sensores de uma vez.

### `cat/` – Compute Average Temperature

- Mantém uma **janela deslizante** com as leituras dos últimos `--janela` segundos (padrão **120**), pelo `timestamp`, e descarta as mais antigas.
- A cada leitura recalcula a média, publica em `caldeira/media` e avalia duas regras:
  - **aumento repentino:** média atual − média anterior ≥ 5 °C;
  - **temperatura alta:** média > 200 °C.
- Loga cada leitura, cada média e cada alerta.

Decisões de projeto:

1. **Cada sensor pesa igual na média** (média das médias de cada sensor na janela). As leituras são periódicas e a janela é por tempo, então cada sensor tem ora k, ora k+1 leituras dentro dela. Numa média simples, o peso de um sensor quente sobe e desce a cada leitura e a média oscila vários graus sem a temperatura mudar. Isso disparava "aumento repentino" a cada 5 s nos testes. Com peso igual, a média só muda quando a temperatura muda.
2. **Só aumento dispara o alerta de aumento repentino.** Uma queda de 5 °C não é um "aumento". Quem quiser alertar também quedas pode usar `--variacao-absoluta` (|Δ| ≥ 5).
3. **Sem spam de temperatura alta:** o alerta sai quando a média *entra* na faixa acima de 200 °C. Para alertar de novo, a média precisa antes cair abaixo de 200 − `--histerese` (padrão 5 °C). `--lembrete N` repete o alerta a cada N segundos enquanto a média continuar alta (desligado por padrão).
4. **Salto padrão de 60 °C no modo pico:** a média dilui o salto de um sensor, que mexe nela por cerca de salto ÷ nº de sensores. Com 4 sensores, um salto de 15 a 30 °C mexeria a média em menos de 5 °C e não dispararia nada. Isso é uma propriedade real do sistema: um pico num único sensor some na média.

### `alarms/` – painel de alarmes

- Assina `caldeira/media` e `caldeira/alertas/#`.
- Interface gráfica em **Tkinter**:
  - média atual em destaque, com a cor da faixa: verde até 190 °C (normal), amarelo de 190 a 200 °C (atenção), vermelho acima de 200 °C (crítica);
  - lista de alarmes com horário, tipo, média anterior e média atual, em **vermelho** para temperatura alta e **laranja** para aumento repentino;
  - o título pisca e o sistema dá um beep quando chega um alarme novo.
- Também imprime cada alarme no console. Com `--console`, roda sem interface gráfica.
- **Thread-safety:** os callbacks do paho rodam na thread de rede, e o Tkinter só pode ser tocado pela thread principal. Os callbacks só colocam eventos numa `queue.Queue`, que a interface esvazia a cada 100 ms com `root.after()`.

Os três usam `paho-mqtt` 2.x com `CallbackAPIVersion.VERSION2`, `client_id` único, reconexão automática (`reconnect_delay_set` + `connect_async`) e broker configurável (`--broker`, `--porta`, padrão `localhost:1883`). As assinaturas ficam no `on_connect`, então são refeitas a cada reconexão.

## Instalação

### 1. Python e dependências

Python 3.10 ou mais novo.

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r sensor/requirements.txt -r cat/requirements.txt -r alarms/requirements.txt
```

O Tkinter vem com o Python no Windows e no macOS (instalador do python.org). No Linux pode ser preciso instalar à parte: `sudo apt install python3-tk` (Debian/Ubuntu) ou `sudo dnf install python3-tkinter` (Fedora).

### 2. Mosquitto (broker)

| Sistema | Instalação | Como subir |
|---|---|---|
| **Windows** | instalador em https://mosquitto.org/download/ | `"C:\Program Files\mosquitto\mosquitto.exe" -v` (ou o serviço "Mosquitto Broker" em `services.msc`) |
| **Linux** (Debian/Ubuntu) | `sudo apt install mosquitto mosquitto-clients` | já sobe como serviço; ou `mosquitto -v` |
| **macOS** | `brew install mosquitto` | `brew services start mosquitto`; ou `mosquitto -v` |
| **Docker** (qualquer SO) | — | `docker run --rm -p 1883:1883 eclipse-mosquitto:2 mosquitto -c /mosquitto-no-auth.conf` |

Desde a versão 2.0, o Mosquitto sem arquivo de configuração só aceita conexões de `localhost`, que é o caso aqui.

Para conferir o tráfego pelo terminal (opcional):

```bash
mosquitto_sub -h localhost -t 'caldeira/#' -v
```

## Como executar

Cada componente em seu próprio terminal, nesta ordem:

```bash
python alarms/alarms.py                              # 1. painel (GUI)
python cat/cat.py                                    # 2. CAT (janela de 120 s)
python sensor/sensor.py --id s1                      # 3. um sensor (a cada 60 s)
```

Com os valores padrão do enunciado (leituras a cada 60 s, janela de 120 s), ver um alarme leva vários minutos. Por isso a demonstração encurta os dois tempos na mesma proporção.

## Roteiro da demonstração (~2 minutos, para o vídeo)

> **Na demonstração, o intervalo dos sensores foi reduzido de 60 s para 5 s, e a janela do CAT de 120 s para 10 s.** A proporção é a mesma do enunciado (janela = 2 × intervalo), só que 12 vezes mais rápida. Os valores padrão continuam sendo 60 s e 120 s.

1. **Subir o Mosquitto** (ver tabela acima) e deixar o terminal visível.
2. **Abrir o Alarms:** `python alarms/alarms.py`. A janela aparece com "aguardando dados do CAT".
3. **Abrir o CAT:** `python cat/cat.py --janela 10`
4. **Subir 3 sensores normais:**
   `./sensor/iniciar_sensores.sh 3 normal 5` (Windows: `sensor\iniciar_sensores.bat 3 normal 5`).
   O CAT loga as leituras e a média fica em torno de 180 °C (verde) no Alarms.
5. **Subir 1 sensor em modo pico:** `python sensor/sensor.py --id p1 --modo pico --intervalo 5`.
   Depois de 3 leituras (~15 s) ele salta para ~240 °C. A média sobe de ~180 °C para ~195 °C e aparece o alarme **Aumento repentino** (laranja).
6. **Subir 1 sensor em modo alta:** `python sensor/sensor.py --id a1 --modo alta --intervalo 5`.
   Em menos de 30 s a média passa de 200 °C, a caixa fica vermelha e aparece o alarme **Temperatura alta** (vermelho).
7. **(Opcional)** Fechar um sensor (Ctrl+C) e subir outro (`--id s9`). O CAT passa a contar o novo sensor sem reiniciar, porque o número de sensores pode variar.

Resultado de um teste real deste roteiro (log do CAT):

```
  *** ALERTA aumento_repentino: 180.5 -> 187.8 °C
  *** ALERTA aumento_repentino: 187.66 -> 195.16 °C
  *** ALERTA temperatura_alta: 199.03 -> 201.07 °C
```

## Captura no Wireshark

1. Interface de loopback: **Adapter for loopback traffic capture** no Windows (Npcap), `lo` no Linux, `lo0` no macOS.
2. Filtro: `mqtt`. O Wireshark reconhece o protocolo na porta 1883. Para ver também o TCP: `tcp.port == 1883`.
3. Comece a captura **antes** de abrir os programas, para pegar as conexões.

O que observar:
- **CONNECT / CONNACK:** um par por processo, com o `client_id` (`sensor-s1`, `cat`, `alarms`) visível no CONNECT;
- **SUBSCRIBE / SUBACK:** o CAT com `caldeira/sensores/+/temperatura`, e o Alarms com `caldeira/media` e `caldeira/alertas/#`;
- **PUBLISH** das leituras (QoS 0): o broker repassa cada uma ao CAT, sem confirmação;
- **PUBLISH + PUBACK** nos alertas (QoS 1): a confirmação aparece nos dois trechos, do CAT para o broker e do broker para o Alarms;
- **PINGREQ / PINGRESP:** o *keepalive* em conexões ociosas.
