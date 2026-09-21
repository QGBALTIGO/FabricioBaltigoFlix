WELCOME = """📦 <b>Rastreio Baltigo</b>

Rastreie encomendas dos Correios, Jadlog, J&T e milhares de transportadoras nacionais e internacionais.

<b>Como usar:</b>
• envie o código de rastreio diretamente;
• ou use <code>/rastrear CODIGO</code>;
• depois eu acompanho a encomenda e aviso quando houver movimentação.

Use /meus para ver seus pacotes ativos e /ajuda para todos os comandos."""

HELP = """🆘 <b>Ajuda</b>

<b>Comandos</b>
/rastrear CODIGO — cadastrar ou consultar
/meus — rastreios ativos
/entregues — encomendas entregues
/transportadoras NOME — procurar transportadora
/config — preferências
/status — estado do bot
/privacidade — dados armazenados
/cancelar — cancelar uma edição
/ajuda — esta ajuda

Você também pode simplesmente enviar um código de rastreio sem comando.

<b>Alertas</b>
⭐ Importantes: postagem/coleta, alfândega, chegada ao destino, saída para entrega, falhas e entrega.
🔔 Todos: qualquer nova movimentação.
🔕 Desativado: mantém o pacote salvo sem alertas."""

NO_SHIPMENTS = """📭 <b>Nenhuma encomenda ativa.</b>

Envie um código de rastreio para começar."""

INVALID_CODE = """❌ Não reconheci isso como um código de rastreio.

Envie apenas o código (5 a 50 caracteres, letras, números ou hífen) ou use:
<code>/rastrear CODIGO</code>"""

PROVIDER_NOT_CONFIGURED = """⚙️ O bot ainda não tem uma chave de provedor de rastreio configurada. O código foi salvo, mas as atualizações só funcionarão após configurar 17TRACK ou Ship24."""
