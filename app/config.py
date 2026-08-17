import os
from dotenv import load_dotenv
from datetime import timedelta

load_dotenv()

class Config:
    SECRET_KEY = os.getenv('SECRET_KEY', 'chave-padrao')
    SQLALCHEMY_DATABASE_URI = os.getenv('DATABASE_URL')
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    # Sessão expira em 1 hora sem atividade
    PERMANENT_SESSION_LIFETIME = timedelta(hours=1)
    SESSION_PERMANENT = True

    # ─────────────────────────────────────────────────────────────────────
    # Módulo API para Pagamentos — Integração Cielo (Link de Pagamento)
    # ─────────────────────────────────────────────────────────────────────
    # Credenciais obtidas com o consultor Cielo (Ecommerce > Link de
    # Pagamento > Configurações > Dados Cadastrais)
    CIELO_CLIENT_ID     = os.getenv('CIELO_CLIENT_ID')
    CIELO_CLIENT_SECRET = os.getenv('CIELO_CLIENT_SECRET')

    # Ambiente: 'sandbox' ou 'producao' — controla qual base URL o
    # cielo_client.py usa. Nunca deixar 'producao' fixo no código.
    CIELO_AMBIENTE = os.getenv('CIELO_AMBIENTE', 'sandbox')

    # Basic Auth do NOSSO endpoint de webhook (nós definimos o valor,
    # não a Cielo — ver decisão registrada na análise [ARQUITETO]).
    # Cadastrar a URL no site Cielo no formato:
    # https://<CIELO_WEBHOOK_USER>:<CIELO_WEBHOOK_PASS>@seudominio.com/api/v1/pagamentos/webhooks/cielo
    CIELO_WEBHOOK_USER = os.getenv('CIELO_WEBHOOK_USER')
    CIELO_WEBHOOK_PASS = os.getenv('CIELO_WEBHOOK_PASS')

    # Regras de negócio fechadas com o cliente
    RECORRENCIA_MAX_TENTATIVAS_NEGADAS = 4  # bloqueia no 5º dia (contagem própria)

    # ─────────────────────────────────────────────────────────────────────
    # Módulo API para Pagamentos — Integração Asaas (Link de Pagamento)
    # ─────────────────────────────────────────────────────────────────────
    ASAAS_API_KEY = os.getenv('ASAAS_API_KEY')
    ASAAS_AMBIENTE = os.getenv('ASAAS_AMBIENTE', 'sandbox')  # 'sandbox' ou 'producao'
    # Token que a Asaas envia no header 'asaas-access-token' de cada webhook —
    # configurado no painel Asaas (Integrações > Webhooks) e conferido aqui
    # pra autenticar a notificação, mesmo padrão de proteção que já fizemos
    # pra Cielo (só que a Asaas oferece esse mecanismo nativo).
    ASAAS_WEBHOOK_TOKEN = os.getenv('ASAAS_WEBHOOK_TOKEN')