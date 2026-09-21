# Rastreio Baltigo

Bot universal de rastreamento para Telegram, com FastAPI, banco de dados, webhooks, API própria e experiência de gerenciamento inspirada em bons rastreadores multi-transportadora, incluindo o Melhor Rastreio. A implementação, identidade e código são próprios e não possuem vínculo com o Melhor Envio.

O index.html antigo da BaltigoFlix foi preservado. O bot vive na pasta app.

## Recursos

### Rastreamento
- Envie somente o código ou use /rastrear CODIGO.
- Autodetecção de transportadora.
- 17TRACK como provedor principal.
- Ship24 como fallback opcional.
- Correios, Jadlog, J&T e milhares de transportadoras conforme cobertura dos provedores.
- Busca imediata das informações após o cadastro, quando o provedor já possui dados.
- Webhooks para atualização automática.
- Polling de redundância configurável a cada 2 horas por até 30 dias.
- Deduplicação global: vários usuários acompanhando o mesmo código não criam vários rastreamentos externos.
- Deduplicação de eventos.

### Experiência de acompanhamento
- /meus com 10 encomendas por página.
- /entregues.
- /buscar por código, apelido, transportadora, status ou texto da última movimentação.
- /filtros por status ou transportadora.
- Página de detalhes no próprio Telegram com:
  - status;
  - código;
  - transportadora;
  - última atualização;
  - local;
  - descrição;
  - histórico;
  - explicação do status.
- Apelido por pacote.
- Parar de acompanhar.
- Atualização manual.
- /relatorio com resumo por status e transportadora.

### Compartilhamento e múltiplos destinatários
Cada rastreio possui botão "Compartilhar rastreio".

O bot gera um deep-link assinado. A pessoa que recebe o link pode adicionar o mesmo pacote ao próprio /meus e receber as movimentações. Isso funciona como múltiplos destinatários de alerta sem duplicar a consulta ao provedor.

Configure SHARE_SECRET com uma string longa e aleatória.

### Alertas
Por pacote:
- Importantes: coleta/postagem, alfândega, chegada ao destino, saída para entrega, falhas e entrega.
- Todos: qualquer nova movimentação.
- Sem alertas.

O monitor também pode avisar uma única vez quando a encomenda fica muito tempo sem nova movimentação. O padrão é 72 horas.

### Segurança antifraude
O bot inclui:
- /seguranca;
- aviso permanente de que o Rastreio Baltigo não envia PIX, boleto ou link de cobrança para liberar encomendas;
- lembrete para validar taxas somente em canais oficiais;
- destaque extra quando a movimentação menciona pagamento/taxa ou fiscalização aduaneira;
- links de compartilhamento assinados;
- Telegram webhook protegido;
- 17TRACK webhook com validação da assinatura;
- Ship24 webhook com Bearer Secret;
- API própria com Bearer Token.

O aviso antifraude não afirma que toda cobrança logística ou fiscal é falsa. Algumas cobranças legítimas podem existir; a orientação é sempre confirmar no canal oficial correspondente.

## Comandos

- /start
- /rastrear CODIGO
- /meus
- /entregues
- /buscar TERMO
- /filtros
- /relatorio
- /transportadoras NOME
- /config
- /seguranca
- /status
- /privacidade
- /cancelar
- /ajuda

Admins:
- /admin
- /broadcast mensagem

## Configuração mínima

Copie .env.example para .env:

    TELEGRAM_BOT_TOKEN=seu_token
    SEVENTEEN_TRACK_TOKEN=sua_chave
    ADMIN_IDS=123456789
    SHARE_SECRET=uma-string-longa-e-aleatoria

Sem 17TRACK ou Ship24, o bot inicia e salva códigos, mas não consegue consultar movimentações externas.

## Rodar localmente

    python -m venv .venv
    pip install -r requirements.txt
    python -m app

## Docker + PostgreSQL

    docker compose up -d --build

Antes de publicar, altere usuário e senha do PostgreSQL do docker-compose.yml.

## Produção com Telegram Webhook

    TELEGRAM_MODE=webhook
    WEBHOOK_BASE_URL=https://seu-dominio.com
    TELEGRAM_WEBHOOK_SECRET=segredo-forte

Endpoint:

    /telegram/webhook

Para teste local, polling é mais simples.

## 17TRACK

Configure:

    SEVENTEEN_TRACK_TOKEN=
    SEVENTEEN_TRACK_VERIFY_SIGNATURE=true
    WEBHOOK_SHARED_SECRET=segredo-forte

Webhook:

    https://SEU-DOMINIO/webhooks/17track?secret=SEU_SEGREDO

## Ship24

Configure:

    SHIP24_API_KEY=
    SHIP24_WEBHOOK_SECRET=
    WEBHOOK_SHARED_SECRET=segredo-forte

Webhook:

    https://SEU-DOMINIO/webhooks/ship24?secret=SEU_SEGREDO

## Polling de redundância

O projeto foi pensado para priorizar webhooks, porque isso reduz consultas e custo.

Se quiser uma verificação periódica adicional:

    FALLBACK_POLLER_ENABLED=true
    POLL_INTERVAL_MINUTES=120
    POLL_TRACKING_DAYS=30

Antes de ativar, confira a cota e a política de cobrança do provedor escolhido.

## Alerta de rastreio parado

Padrão:

    STALE_AFTER_HOURS=72
    STALE_MONITOR_ENABLED=true
    STALE_CHECK_INTERVAL_MINUTES=60

O alerta é deduplicado. Se a encomenda continuar parada no mesmo evento, o usuário não recebe a mesma mensagem repetidamente. Uma nova movimentação reinicia o ciclo.

## API própria

Defina:

    PUBLIC_API_TOKEN=

Consulta:

    GET /api/v1/track/NM123456789BR
    Authorization: Bearer SEU_TOKEN

A resposta inclui status, transportadora, histórico, tempo desde a última atualização e indicador stale.

## Banco

Desenvolvimento:

    sqlite+aiosqlite:///./tracker.db

Produção:

    postgresql+asyncpg://usuario:senha@host:5432/banco

A tabela notification_logs é criada automaticamente e serve para deduplicar alertas operacionais, como rastreios sem atualização.

## Estrutura

app/bot: comandos, callbacks, paginação, filtros e compartilhamento.
app/providers: 17TRACK e Ship24.
app/services/tracking.py: regras de rastreio.
app/services/notifier.py: notificações de movimentação.
app/services/monitor.py: alerta de rastreio parado e polling opcional.
app/models.py: banco.
app/main.py: FastAPI, API e webhooks.
tests: testes automatizados.

## Observação sobre cobertura

Nenhum agregador garante literalmente todo código existente. Algumas transportadoras exigem CPF/CNPJ, CEP, país de destino, contrato ou outras credenciais. A arquitetura usa adapters e pode receber novas integrações sem reescrever o bot inteiro.
