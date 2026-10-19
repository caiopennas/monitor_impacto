# Impacto Local — servidor da empresa

Esta implantação funciona na rede da empresa, sem Cloudflare, GitHub Pages ou domínio externo. O código e as telas existentes são preservados; a execução passa para Waitress e um serviço do Windows independente da sessão do usuário.

## Acesso

- No servidor: `http://localhost:8130/`.
- Em computadores da mesma rede: `http://ASUS_FIGITAL:8130/` ou o IP atual do servidor, porta 8130.
- Login, contas, cadastros e ordem das impressoras são migrados. Nenhuma senha padrão nova é criada.
- Câmeras, painel completo e mosaico Mainsail continuam disponíveis. Mainsail precisa estar instalado na impressora e acessível pela rede do navegador. Bambu Studio deve ser aberto manualmente na área de trabalho.

O nome do computador é estável, mas sua resolução depende da rede. Para manter também o IP estável, reserve o endereço do servidor no DHCP do roteador. Não configure um IP estático sem confirmar faixa, gateway e disponibilidade.

## Execução e recuperação

O serviço `ImpactoLocal` inicia automaticamente com o Windows, inclusive antes do login. Ele executa como `NT AUTHORITY\LocalService`, sem privilégios administrativos. O supervisor reinicia o processo web se ele encerrar ou falhar três verificações de saúde consecutivas. O Windows reinicia o supervisor se ele falhar. Threads de coleta reiniciam após exceções; imagens e leituras antigas não são tratadas como atuais.

Isso não garante disponibilidade durante falta de energia, suspensão, falha de hardware ou interrupção da rede. O servidor deve ficar ligado e sem suspensão quando conectado à tomada. Não é necessário manter navegador ou terminal abertos.

## Arquivos da instalação

- `C:\ProgramData\ImpactoLocal\app`: código em execução.
- `C:\ProgramData\ImpactoLocal\venv`: ambiente Python.
- `C:\ProgramData\ImpactoLocal\data`: usuários, auditoria, credenciais LAN, impressoras e configuração local.
- `C:\ProgramData\ImpactoLocal\logs`: logs com rotação.
- `C:\ProgramData\ImpactoLocal\install-result.json`: resultado da instalação e teste de recuperação.

Não publique a pasta data no GitHub. Faça backup privado dos dados. Para copiar o banco SQLite, use sua API de backup ou pare o serviço antes de copiar. A instalação preserva os arquivos antigos como origem de migração; depois da migração eles não recebem novas edições.

O ambiente virtual usa o Python base informado durante a instalação. Não remova esse Python. LocalService recebe somente leitura/execução no runtime e código e permissão de escrita nas pastas data/logs.

## Rede e administração

Waitress escuta na porta 8130; Host/Origin e login continuam validados. A regra `ImpactoLocal-LAN` aceita somente TCP 8130 de `192.168.1.0/24`. Nenhuma porta do roteador é aberta e nenhum túnel é iniciado. Esta implantação usa HTTP dentro da rede local; não a exponha diretamente à internet.

Para ver o estado, use `Get-Service ImpactoLocal`. Para iniciar/parar/reiniciar, use Services (`services.msc`) ou `Start-Service`, `Stop-Service`, `Restart-Service`, em terminal administrador. Editar firewall, conta do serviço ou seus diretórios protegidos também exige administrador.

`install-local-service.ps1` ativa um pacote previamente preparado (runtime, app, dados, WinSW e XML). Ele testa recuperação encerrando somente o processo web identificado como `local_app.py`; não envia comandos às impressoras. O notebook/servidor não é reiniciado durante esse teste.

## Testes

```sh
python -m pip install -r requirements-local.txt
python -m unittest test_server.py test_access.py test_local.py
```

O adaptador WSGI reutiliza as rotas autenticadas existentes, mas o parsing HTTP, limites e conexões ficam a cargo do Waitress. `local_app.py` não abre o servidor HTTP antigo. `local_supervisor.py` é a entrada usada pelo serviço.
