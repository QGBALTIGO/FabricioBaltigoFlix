# Melhor Rastreio 📦

Bot de rastreamento para Telegram com FastAPI, PostgreSQL, notificações automáticas, scanner de etiquetas e múltiplas fontes de rastreio.

A arquitetura é híbrida: combina fontes públicas/diretas, fallbacks e health persistente para reduzir dependência de um único serviço.

## Motor de rastreamento

Ordem principal:

1. Melhor Rastreio GraphQL, usando a operação pública `searchParcel`.
2. Correios direto, quando o código tem formato postal brasileiro.
3. Jadlog direto.
4. Total Express direto.
5. 17TRACK e Ship24 somente quando configurados como fallbacks opcionais.

O catálogo cobre diretamente ou pelo Melhor Rastreio, entre outras:

- Correios
- Jadlog
- J&T Express
- Loggi
- LATAM Cargo
- Azul Cargo
- Buslog
- Viação Mundo
- Melhor Envio
- Total Express

## Health, quarentena e polling

Cada fonte possui health persistente no PostgreSQL.

Erros reais de rede, HTTP ou parser aumentam o contador de falhas:

- 3 falhas consecutivas: pausa por 1 hora
- 6 falhas consecutivas: pausa por 6 horas
- 12 falhas consecutivas: pausa por 24 horas

Um código simplesmente não encontrado não é tratado como falha técnica da fonte.

Cadência padrão de polling:

- sem eventos / pré-postagem: 60 min
- coletado / em trânsito: 30 min
- chegou à região de destino: 15 min
- saiu para entrega: 5 min
- fiscalização / falha / exceção / retirada: 30 min
- entregue: encerra consultas

O mesmo código é consultado uma única vez mesmo quando vários usuários o acompanham.

## Telegram

Comandos principais:

- `/rastrear CODIGO`
- `/meus`
- `/resumo`
- `/entregues`
- `/arquivo`
- `/relatorio`
- `/config`
- `/privacidade`
- `/ajuda`

Administração:

- `/admin`
- `/saude`

Também é possível enviar o código diretamente no chat.

### Entrada inteligente

Além do código puro, o bot pode:

- detectar códigos em mensagens de lojas e transportadoras;
- receber mensagens encaminhadas;
- analisar fotos de etiquetas e capturas de tela;
- detectar, quando presentes, loja, número do pedido e nome do produto;
- pedir confirmação antes de salvar qualquer código detectado automaticamente.

### Scanner híbrido de etiquetas

O fluxo de imagem é:

1. QR Code / código de barras com ZXing C++;
2. validação do conteúdo como possível rastreio;
3. OCR local com RapidOCR + ONNX Runtime somente quando necessário;
4. confirmação do usuário antes de cadastrar.

EAN/UPC numérico de produto não é aceito automaticamente como rastreio. O scanner limita resolução e possui timeout/cooldown para proteger CPU e memória.

### Visão geral

**📦 Visão geral** mostra somente categorias que realmente possuem encomendas no momento:

- saiu para entrega;
- chegou à região de destino;
- precisa de atenção;
- está em trânsito;
- foi entregue no dia.

Quando há histórico suficiente da mesma transportadora, pode mostrar uma janela estimada baseada em entregas anteriores. Sem amostra suficiente, nenhuma previsão artificial é inventada.

### Cartão de rastreio

O cartão principal segue uma hierarquia única:

1. nome da encomenda;
2. status;
3. última movimentação real;
4. rota/local atual;
5. previsão de entrega, quando existente;
6. tempo desde a última atualização.

As ações ficam reduzidas a:

- Histórico
- Alertas
- Compartilhar rastreio

### Alertas

Cada encomenda possui um único botão **🔔 Alertas**. Nele o usuário pode:

- ligar ou desligar todos os alertas;
- escolher movimentações intermediárias;
- escolher saída para entrega;
- escolher problemas, fiscalização e retirada;
- escolher entrega concluída.

Não existem horários silenciosos.

### Arquivamento inteligente

Entregas concluídas permanecem em **Entregues recentes** pelo período configurado em `DELIVERED_ARCHIVE_AFTER_DAYS` (padrão: 7 dias).

Depois disso aparecem automaticamente em **🗃 Arquivo**. A classificação é calculada pela data real de entrega, portanto não depende de cron/job e continua correta após reinícios ou deploys.

Entregas concluídas não consomem o limite de rastreios ativos.

### Saúde administrativa

O painel `/admin` / `/saude` mostra, entre outros:

- pacotes e acompanhamentos ativos;
- backlog do polling;
- consultas por hora;
- latência e falhas por fonte;
- latência por transportadora;
- fonte mais rápida;
- fontes em quarentena;
- sucesso/falha de notificações;
- taxa de sucesso de QR/código de barras e OCR;
- erros técnicos recentes.

A telemetria operacional não guarda código de rastreio, usuário, endereço ou conteúdo da encomenda. Ela possui retenção curta, configurável, com padrão de 7 dias.

## Banco

Tabelas principais:

- users
- shipments
- subscriptions
- tracking_events
- notification_logs
- provider_health
- polling_states
- subscription_preferences
- operational_events

Tabelas legadas `user_preferences` e `deferred_notifications` são mantidas apenas para compatibilidade/drenagem de versões antigas.

## Produção

No Railway, o Telegram roda via webhook e o PostgreSQL persiste usuários, encomendas, eventos, estado das fontes e telemetria operacional.

Configurações relevantes:

```env
MELHOR_RASTREIO_ENABLED=true
DIRECT_FALLBACKS_ENABLED=true
TRACKING_POLLER_ENABLED=true
MONITOR_TICK_MINUTES=5
DELIVERED_ARCHIVE_AFTER_DAYS=7
IMAGE_BARCODE_SCAN_ENABLED=true
IMAGE_OCR_ENABLED=true
TELEMETRY_RETENTION_DAYS=7
```

## Segurança e privacidade

- tokens ficam apenas em variáveis do Railway;
- nenhuma chave é commitada;
- webhook do Telegram usa secret token;
- API própria usa bearer token;
- links compartilháveis são assinados;
- logs do httpx/httpcore são reduzidos para evitar exposição de token;
- imagens são processadas localmente e não ficam armazenadas após a leitura;
- códigos detectados em texto ou imagem só são cadastrados depois da confirmação do usuário;
- telemetria operacional não contém identificadores de usuário ou encomenda.

## Testes

O CI executa:

- instalação das dependências;
- OpenCV headless;
- construtor real do RapidOCR;
- `compileall`;
- suíte completa do pytest;
- build do container `python:3.12-slim`;
- testes reais de OCR, QR Code e código de barras dentro do container de produção.

Execução local:

```bash
python -m compileall -q app
python -m pytest -q
```
