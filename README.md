# Monitor Impacto

Central de impressoras 3D com mosaico de câmeras, telemetria, cadastro de K1 Max e Bambu A1 mini, contas individuais e registro de atividades.

**Instalação permanente na empresa:** veja [Impacto Local](LOCAL.md), com serviço Windows, servidor Waitress e reinício automático. Os comandos abaixo descrevem a execução manual original.

## Instalar no Windows

1. Instale Python 3.12 com a opção de adicionar ao PATH.
2. Baixe e extraia o repositório. Execute `Instalar.cmd`.
3. Execute `Criar administrador.cmd`: escolha usuário e senha de pelo menos 12 caracteres. A senha é digitada no terminal sem aparecer. Não existe senha padrão nem cadastro público.
4. Execute `Iniciar Monitor.cmd`, mantenha a janela aberta e acesse <http://127.0.0.1:8130>.
5. Entre na conta e cadastre as impressoras. Em **Usuários e atividades**, crie as contas da equipe.

O servidor só escuta em `127.0.0.1:8130`. Se a porta já estiver ocupada, feche a instância antiga do monitor antes de iniciar esta versão. Não é necessário parar impressões.

O código publicado começa sem impressoras cadastradas. Na instalação existente, `printers.json` é preservado. O repositório não contém IPs, números de série ou credenciais reais.

## Contas e permissões

| Perfil | Permissões no aplicativo |
| --- | --- |
| Visualizador | Ver o painel, imagens e estado; alterar a própria senha; sair |
| Administrador | Tudo acima, cadastrar/editar impressoras, buscar na rede, abrir interfaces externas e gerenciar usuários/histórico |

Desativar uma conta, alterar seu perfil ou redefinir sua senha encerra suas sessões. O aplicativo impede desativar ou rebaixar o último administrador ativo. Sessões duram no máximo oito horas. Senhas usam PBKDF2-SHA256 com 600 mil iterações e salt individual; sessões aleatórias ficam em cookies HttpOnly/SameSite e são armazenadas apenas como hash no banco. Há limite de tentativas de login por origem de conexão e usuário em janela de 15 minutos.

No primeiro acesso, a conta administrativa só pode ser criada pelo terminal local. A recuperação das contas da equipe é feita por outro administrador. Ainda não há recuperação por e-mail, MFA ou recuperação automática do último administrador: guarde essa credencial em gerenciador de senhas.

## Histórico

A administração consulta até 100 eventos por página, filtra por usuário e navega aos anteriores. Registra entradas válidas e inválidas, saída, abertura do painel, alterações de contas/senhas, resultados das solicitações de cadastro, busca na rede e abertura do Bambu Studio. A ampliação de câmera é identificada como evento informado pela interface.

O histórico não registra o conteúdo das senhas, códigos LAN, imagens ou corpos das requisições. Horários são armazenados como UTC e exibidos no fuso do navegador. Não há expurgo automático: faça backup e defina sua política de retenção antes de usar com uma equipe grande.

O log é operacional em SQLite; um administrador do computador pode alterá-lo. Não é um registro inviolável. Contas são do Monitor Impacto, não da impressora. Ações realizadas em Mainsail, Fluidd, Creality Print, Bambu Studio ou diretamente no IP da impressora não são atribuídas a essas contas. O perfil visualizador não substitui as permissões de rede/firewall das impressoras. O Monitor Impacto não envia comandos de iniciar, pausar, aquecer ou cancelar impressão.

## Dados privados e backup

- `printers.json`: cadastros locais, ao lado de `server.py`.
- `%LOCALAPPDATA%\ImpactoMonitor\accounts.sqlite3`: contas, sessões e auditoria.
- `%LOCALAPPDATA%\ImpactoMonitor\printer-secrets.json`: credenciais LAN da Bambu, quando salvas.
- `IMPACTO_DATA_DIR`: permite escolher outra pasta para o banco de contas.
- `remote-access.json`: configuração privada de acesso Tailscale.

Não adicione esses arquivos ao GitHub. Faça backup em local privado, com o monitor parado, e restrinja acesso à conta Windows que o executa. Nunca use credenciais de produção em testes.

## GitHub e acesso externo

GitHub guarda o código e executa os testes a cada atualização. **Publicar este repositório não coloca o servidor na internet.** GitHub Pages não executa Python, sessões ou SQLite. Veja [DEPLOY.md](DEPLOY.md) para os requisitos de acesso remoto.

## Testes

```sh
python -m pip install -r requirements.txt
python -m unittest test_server.py test_access.py
```

Os testes usam banco temporário e servidor HTTP local isolado, sem enviar comandos às impressoras. Cobrem autenticação, câmera/telemetria protegidas, origem das requisições, autorização de visualizador, revogação/expiração, proteção do último administrador, limite de login, histórico e cadastros.
