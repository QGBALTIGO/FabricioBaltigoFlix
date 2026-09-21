# Rastreio Baltigo

Bot universal de rastreio para Telegram, com FastAPI, banco de dados, webhooks e API HTTP própria.

O index.html antigo da BaltigoFlix foi preservado. O bot vive na pasta app.

## Recursos implementados

- Cadastro enviando somente o código ou usando /rastrear CODIGO.
- Autodetecção de transportadora.
- 17TRACK como provedor principal.
- Ship24 como fallback opcional.
- Correios, Jadlog, J&T e milhares de transportadoras conforme a cobertura do provedor.
- /meus e /entregues.
- Apelido por encomenda.
- Histórico de eventos.
- Atualização manual.
- Alertas importantes, todos os eventos ou silenciado.
- Deduplicação de eventos.
- Deduplicação global do mesmo código entre vários usuários.
- Webhooks 17TRACK e Ship24.
- Verificação da assinatura SHA-256 da 17TRACK.
- Verificação do Bearer Secret do Ship24.
- Telegram por polling ou webhook.
- API própria em /api/v1/track/{codigo}.
- Rate limit simples da API.
- /admin e /broadcast para IDs autorizados.
- SQLite para desenvolvimento.
- PostgreSQL para produção.
- Docker e Docker Compose.
- Configuração Railway.
- Health check em /health.
- Testes automáticos no GitHub Actions.

## Configuração mínima

Copie .env.example para .env e preencha:

    TELEGRAM_BOT_TOKEN=seu_token
    TELEGRAM_MODE=polling
    SEVENTEEN_TRACK_TOKEN=sua_chave

Sem uma chave 17TRACK ou Ship24, o bot inicia e salva códigos, mas não consegue buscar movimentações externas.

## Rodar localmente

    python -m venv .venv
    pip install -r requirements.txt
    python -m app

A aplicação sobe por padrão na porta 8000.

## Docker + PostgreSQL

    docker compose up -d --build

Antes de publicar, altere usuário e senha do PostgreSQL no docker-compose.yml.

## Telegram

No BotFather, crie o bot e copie o token.

Comandos disponíveis:

- /start
- /rastrear CODIGO
- /meus
- /entregues
- /transportadoras NOME
- /config
- /status
- /privacidade
- /cancelar
- /ajuda

Qualquer texto que tenha formato de código de rastreio também é interpretado automaticamente.

## 17TRACK

Configure SEVENTEEN_TRACK_TOKEN.

No painel da 17TRACK, cadastre o webhook:

    https://SEU-DOMINIO/webhooks/17track?secret=SEU_SEGREDO

E configure:

    WEBHOOK_SHARED_SECRET=SEU_SEGREDO
    SEVENTEEN_TRACK_VERIFY_SIGNATURE=true

A aplicação valida também o header sign oficial da 17TRACK usando SHA-256.

## Ship24

Configure:

    SHIP24_API_KEY=
    SHIP24_WEBHOOK_SECRET=

Webhook:

    https://SEU-DOMINIO/webhooks/ship24?secret=SEU_SEGREDO

O endpoint valida Authorization: Bearer com o segredo configurado.

## Telegram em webhook

Para produção:

    TELEGRAM_MODE=webhook
    WEBHOOK_BASE_URL=https://seu-dominio.com
    TELEGRAM_WEBHOOK_SECRET=um-segredo-forte

O endpoint usado será /telegram/webhook.

Para testes locais, polling é mais simples.

## API própria

Defina PUBLIC_API_TOKEN.

Consulta:

    GET /api/v1/track/NM123456789BR
    Authorization: Bearer SEU_TOKEN

A API só consulta códigos que já existem no banco do bot, evitando usar a cota externa como API pública irrestrita.

## Administração

Defina IDs numéricos separados por vírgula:

    ADMIN_IDS=123456789,987654321

Comandos:

- /admin
- /broadcast mensagem

## Estrutura

app/bot contém os comandos e callbacks do Telegram.
app/providers contém integrações 17TRACK e Ship24.
app/services contém regras de rastreio e notificações.
app/models.py contém o banco.
app/main.py contém FastAPI, API própria e webhooks.
tests contém testes unitários.

## Segurança

- Nenhum token é salvo no Git.
- .env fica ignorado.
- Webhook Telegram valida secret token.
- Webhook 17TRACK valida assinatura.
- Webhook Ship24 valida bearer secret.
- API própria exige bearer token.
- CPF/CNPJ não é coletado nesta versão.
- Rastreios são tratados no privado do Telegram.

## Cobertura

Nenhum agregador garante literalmente todo código de envio existente. Algumas transportadoras exigem dados adicionais, contrato, CEP, país de destino ou credenciais. A arquitetura foi feita para receber novos adapters sem reescrever o bot.
