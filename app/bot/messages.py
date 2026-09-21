WELCOME = """📦 <b>Rastreio Baltigo</b>

Acompanhe Correios, Jadlog, J&T, Loggi, LATAM, Azul, Buslog, Total Express e outras fontes em um só lugar.

<b>Como usar</b>
• envie o código de rastreio diretamente;
• ou use <code>/rastrear CODIGO</code>;
• eu salvo o pacote e aviso quando houver movimentação;\n• o bot troca de fonte automaticamente se uma delas ficar indisponível.

Você também pode pesquisar seus rastreios, filtrar por status/transportadora, compartilhar o acompanhamento e receber aviso quando uma encomenda ficar muito tempo sem atualização.

🛡 <b>Segurança:</b> nunca enviaremos cobrança, PIX ou boleto para liberar encomendas. Confirme qualquer taxa somente em canais oficiais."""

HELP = """🆘 <b>Ajuda</b>

<b>Rastreios</b>
/rastrear CODIGO — cadastrar e acompanhar
/meus — ver pacotes ativos
/entregues — ver entregues
/buscar TERMO — procurar por código, nome, transportadora ou status
/filtros — filtrar por status ou transportadora
/relatorio — resumo dos seus envios
/transportadoras NOME — pesquisar transportadoras

<b>Preferências</b>
/config — como funcionam os alertas
/seguranca — orientações antifraude
/status — estado do bot
/privacidade — dados armazenados
/cancelar — cancelar uma edição
/ajuda — esta ajuda

<b>Alertas</b>
⭐ Importantes: postagem/coleta, alfândega, chegada ao destino, saída para entrega, falhas e entrega.
🔔 Todos: qualquer nova movimentação.
🔕 Desativado: mantém o pacote salvo sem alertas.

💡 Você também pode simplesmente enviar um código de rastreio sem comando."""

SECURITY = """🛡 <b>Rastreio seguro</b>

• O Rastreio Baltigo não envia PIX, boleto ou link de cobrança para liberar encomendas.
• Não faça pagamentos por links recebidos por SMS, WhatsApp, e-mail ou anúncio sem confirmar no canal oficial da loja, transportadora ou órgão responsável.
• Cobranças fiscais legítimas podem existir em alguns envios; confirme sempre diretamente no canal oficial antes de pagar.
• Nunca envie senha, código de autenticação ou dados bancários pelo chat do bot.

Se uma movimentação mencionar taxa, retenção ou pagamento, trate o aviso apenas como informação e valide a cobrança fora do bot."""

NO_SHIPMENTS = """📭 <b>Nenhuma encomenda ativa.</b>

Envie um código de rastreio para começar."""

INVALID_CODE = """❌ Não reconheci isso como um código de rastreio.

Envie apenas o código (5 a 50 caracteres, letras, números ou hífen) ou use:
<code>/rastrear CODIGO</code>"""
