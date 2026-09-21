# Melhor Rastreio 📦

Bot de rastreamento para Telegram, com FastAPI, PostgreSQL, notificações automáticas e múltiplas fontes de rastreio.

O projeto usa uma arquitetura híbrida para não depender de uma API paga.

## Motor de rastreamento

Ordem principal:

1. Melhor Rastreio GraphQL, usando a operação pública `searchParcel`.
2. Correios direto, quando o código tem formato postal brasileiro.
3. Jadlog direto pela página pública.
4. Total Express direto pelo endpoint público do rastreador.
5. 17TRACK e Ship24 apenas se forem configurados manualmente como fallbacks opcionais.

Atualmente o catálogo cobre diretamente ou pelo Melhor Rastreio:

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

A arquitetura permite adicionar novos adapters sem alterar o restante do bot.

## Health e quarentena

Cada fonte possui health persistente em PostgreSQL.

Erros reais de rede, HTTP ou parser aumentam o contador de falhas:

- 3 falhas consecutivas: pausa por 1 hora
- 6 falhas consecutivas: pausa por 6 horas
- 12 falhas consecutivas: pausa por 24 horas

Um código simplesmente "não encontrado" NÃO conta como falha da fonte. Nesse caso o roteador tenta o próximo provider.

Uma resposta válida zera o contador de falhas.

## Polling inteligente

O mesmo código é consultado uma única vez, mesmo se vários usuários o acompanham.

Cadência padrão:

- sem eventos / pré-postagem: 60 min
- coletado / em trânsito: 30 min
- chegou ao destino: 15 min
- saiu para entrega: 5 min
- alfândega / falha / exceção / retirada: 30 min
- entregue: encerra consultas

O worker acorda a cada 5 minutos e consulta apenas os pacotes cujo `next_check_at` venceu.

## Telegram

Comandos principais:

- /rastrear CODIGO
- /meus
- /entregues
- /buscar TERMO
- /filtros
- /relatorio
- /transportadoras
- /config
- /seguranca
- /status
- /privacidade
- /ajuda

Também é possível simplesmente enviar o código no chat.

### Caixa de entrada inteligente

Além do código puro, o bot pode:

- detectar códigos dentro de mensagens de lojas/transportadoras;
- receber mensagens encaminhadas e sugerir o rastreio encontrado;
- ler prints localmente por OCR, sem enviar a imagem para um serviço de IA/OCR externo;
- detectar, quando presentes, loja, número do pedido e nome do produto;
- pedir confirmação antes de cadastrar qualquer código detectado automaticamente.

### Visão geral

O botão **📦 Visão geral** resume o estado atual dos pacotes sem exibir categorias zeradas.

Ele destaca apenas o que existe no momento:

- saiu para entrega;
- chegou à região de destino;
- precisa de atenção;
- está em trânsito;
- foi entregue no dia.

Quando há histórico suficiente da mesma transportadora, a visão geral também pode mostrar uma janela estimada usando entregas anteriores, sem inventar datas quando não há amostra suficiente.

### Alertas avançados

Cada pacote possui um único botão **🔔 Alertas**. Dentro dele, o usuário pode:

- ativar ou desativar todos os alertas;
- escolher movimentações intermediárias;
- escolher saída para entrega;
- escolher problemas, fiscalização e retirada;
- escolher entrega concluída.

Não há horários silenciosos: as notificações habilitadas são enviadas assim que a movimentação é processada.

### Orientação de status

Cada cartão pode mostrar uma orientação curta em **💡 Agora**, integrada ao próprio rastreio, sem criar um botão ou tela extra.

## Banco

Tabelas principais:

- users
- shipments
- subscriptions
- tracking_events
- notification_logs
- provider_health
- polling_states
- user_preferences
- subscription_preferences
- deferred_notifications

`provider_health` guarda a saúde das fontes.
`polling_states` controla quando cada encomenda deve ser consultada novamente.
`subscription_preferences` guarda alertas e metadados opcionais por encomenda.
`user_preferences` e `deferred_notifications` permanecem apenas para compatibilidade e drenagem de dados criados pela antiga função de horário silencioso.

## Melhor Rastreio GraphQL

O provider principal usa:

`https://api.melhorrastreio.com.br/graphql`

com fallback para:

`https://melhor-rastreio-api.melhorrastreio.com.br/graphql`

Ele usa apenas a operação pública `searchParcel`. Funções GraphQL que exigem autenticação não são usadas.

Importante: esse endpoint é um backend público do serviço Melhor Rastreio, e não uma API de terceiros com SLA garantido para nosso projeto. Por isso existem fallbacks, health-check e quarentena automática.

## Produção

No Railway:

```
MELHOR_RASTREIO_ENABLED=true
DIRECT_FALLBACKS_ENABLED=true
TRACKING_POLLER_ENABLED=true
MONITOR_TICK_MINUTES=5
```

O Telegram roda via webhook e o PostgreSQL persiste usuários, pacotes, eventos e estado dos providers.

## Segurança

- tokens ficam apenas em variáveis do Railway;
- nenhuma chave é commitada;
- webhook Telegram usa secret token;
- API própria usa bearer token;
- links compartilháveis são assinados;
- logs do httpx/httpcore são silenciados para não expor token do Telegram;
- OCR de prints roda localmente e o arquivo temporário é removido após a leitura;
- códigos detectados em texto ou imagem só são cadastrados depois da confirmação do usuário.

## Testes

```bash
python -m compileall -q app
python -m pytest -q
```
