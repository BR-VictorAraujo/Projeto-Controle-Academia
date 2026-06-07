# 🏋️ Sistema de Controle de Acesso — Academia

Sistema completo de gestão de academia com controle de acesso biométrico, desenvolvido em Python/Flask com PostgreSQL.

---

## 🚀 Funcionalidades

- **Cadastro de alunos** — dados completos, foto, plano e vencimento
- **Controle financeiro** — pagamentos, formas de pagamento, relatórios
- **Acesso biométrico** — leitor Digital Persona U.are.U 4500
- **Portaria** — reconhecimento em tempo real com feedback visual
- **Monitoramento** — tela em tempo real com dados do aluno (CPF, telefone, plano)
- **Relatórios** — exportação de dados por período
- **Parâmetros** — configuração completa do sistema
- **Múltiplos usuários** — perfis admin e operador

---

## 🛠️ Stack

| Componente | Tecnologia |
|---|---|
| Backend | Python 3.14 + Flask |
| Banco de dados | PostgreSQL 16 |
| ORM | SQLAlchemy |
| Frontend | Bootstrap 5 + Jinja2 |
| App biometria | CustomTkinter |
| Biometria | Digital Persona U.are.U 4500 |

---

## 📋 Pré-requisitos

- Windows 10 x64
- Python 3.14
- PostgreSQL 16
- Driver Legacy U.are.U 4500 (`biometria/setup_x64.msi`)
- SDK DigitalPersona (`biometria/finger Printer Driver & SDK.rar`)

---

## ⚡ Instalação rápida

### 1. Clonar o repositório
```bash
git clone https://github.com/BR-VictorAraujo/Projeto-Controle-Academia.git Sistema_controle_acesso
cd Sistema_controle_acesso
```

### 2. Criar ambiente virtual e instalar dependências
```bash
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
pip install psycopg[binary]
```

### 3. Criar arquivo de configuração
Criar o arquivo `.env` na raiz do projeto:
```env
DATABASE_URL=postgresql+psycopg://postgres:SENHA@localhost:5432/academia_db
SECRET_KEY=chave-secreta-sistema-2024
```

### 4. Criar o banco de dados
```sql
CREATE DATABASE academia_db;
```

### 5. Iniciar o sistema
```bash
python run.py
```

Acesse: **http://127.0.0.1:5000**  
Login padrão: `admin` / `admin123`

---

## 🔬 App de Biometria

### Instalação
1. Instalar driver: `biometria/setup_x64.msi` (como Administrador)
2. Instalar SDK: `biometria/finger Printer Driver & SDK.rar` (como Administrador)
3. Conectar o leitor U.are.U 4500 via USB
4. Verificar em **Gerenciador de Dispositivos → Authentication Devices**

### Executar
```bash
python biometria/app.py
```

### DLLs necessárias (já incluídas em `biometria/`)
- `uareu4500.dll`
- `dpfj.dll`
- `dpfpdd.dll`
- `libwinpthread-1.dll`

---

## ⚙️ Auto-start (Windows)

### Sistema web (inicia com o Windows)
```powershell
schtasks /create /tn "AcademiaWeb" /tr "C:\Sistema_controle_acesso\venv\Scripts\python.exe C:\Sistema_controle_acesso\run.py" /sc onstart /ru SYSTEM /f
```

### App biometria (inicia com o login)
```powershell
# Verificar usuário atual com: whoami
schtasks /create /tn "AcademiaBiometria" /tr "C:\Sistema_controle_acesso\venv\Scripts\pythonw.exe C:\Sistema_controle_acesso\biometria\app.py" /sc onlogon /ru "MAQUINA\USUARIO" /f
```

---

## 📁 Estrutura do projeto

```
Sistema_controle_acesso/
├── app/
│   ├── __init__.py
│   ├── config.py
│   ├── models.py
│   └── routes.py
├── biometria/
│   ├── app.py              # Interface CustomTkinter
│   ├── bio_db.py           # Acesso ao banco
│   ├── bio_reader.py       # Integração com leitor
│   ├── bio_config.py       # Configurações
│   ├── uareu4500.dll       # SDK wrapper
│   ├── dpfj.dll            # Processamento FMD
│   ├── dpfpdd.dll          # Captura
│   └── libwinpthread-1.dll # Dependência MinGW
├── templates/              # HTML Jinja2
├── static/                 # CSS, JS, imagens
├── run.py                  # Entry point
├── requirements.txt
└── .env                    # Configurações (não commitado)
```

---

## 🔒 Segurança

- Autenticação por sessão com timeout de 1 hora
- Senhas com hash bcrypt
- Sistema projetado para rede local (sem exposição à internet)

---

## 🐛 Problemas comuns

| Erro | Solução |
|---|---|
| `No module named 'dotenv'` | `pip install python-dotenv` |
| `No pq wrapper available` | `pip install psycopg[binary]` |
| `DATABASE_URL not set` | Verificar arquivo `.env` |
| Leitor não encontrado | Instalar driver Legacy + SDK |
| `DEVICE_BUSY` | `net stop WbioSrvc && net start WbioSrvc` |
| ExecutionPolicy error | `Set-ExecutionPolicy RemoteSigned -Scope CurrentUser` |

---

## 📄 Documentação

Consulte o arquivo `ROTEIRO_INSTALACAO.docx` para o guia completo de instalação passo a passo.

---

## 👨‍💻 Desenvolvimento

Desenvolvido com Python/Flask para ambiente Windows com leitor biométrico Digital Persona U.are.U 4500.
