WELCOME = """📦 <b>Bem-vindo, {name}!</b>

Para rastrear uma encomenda, envie o código dela. Se quiser adicionar um nome, digite o código e o nome juntos.

<b>Exemplo:</b>
<code>PN123456789BR Minha encomenda 😊🚚</code>

Escolha uma opção no menu abaixo:

• Envie um código de rastreio para cadastrar
• Use "📦 Meus pacotes" para consultar status
• Use "🗑 Remover pacote" para excluir um código"""

HELP = """🆘 <b>Como usar</b>

Envie o código de rastreio diretamente no chat.

Se quiser dar um nome ao pacote, escreva o código e o nome na mesma mensagem:

<code>PN123456789BR Minha encomenda 😊🚚</code>

Depois use os botões do menu:
• 📦 Meus pacotes — consultar seus rastreios
• 🗑 Remover pacote — excluir um código salvo

O bot busca automaticamente e avisa quando houver novas movimentações."""

SECURITY = """🛡 <b>Rastreio seguro</b>

• O Rastreio Baltigo não envia PIX, boleto ou link de cobrança para liberar encomendas.
• Não faça pagamentos por links recebidos por SMS, WhatsApp, e-mail ou anúncio sem confirmar no canal oficial da loja, transportadora ou órgão responsável.
• Cobranças fiscais legítimas podem existir em alguns envios; confirme sempre diretamente no canal oficial antes de pagar.
• Nunca envie senha, código de autenticação ou dados bancários pelo chat do bot.

Se uma movimentação mencionar taxa, retenção ou pagamento, trate o aviso apenas como informação e valide a cobrança fora do bot."""

NO_SHIPMENTS = """📭 <b>Nenhuma encomenda ativa.</b>

Envie um código de rastreio para começar."""

INVALID_CODE = """❌ Não reconheci um código de rastreio válido.

Envie o código sozinho ou junto com um nome.

<b>Exemplo:</b>
<code>PN123456789BR Minha encomenda 😊🚚</code>"""
