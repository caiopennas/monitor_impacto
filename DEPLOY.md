# Disponibilizar o Monitor Impacto

## Opção atual: escritório + Tailscale

O computador do escritório executa este servidor, conectado às impressoras. Cada usuário remoto instala Tailscale e entra na rede privada autorizada. O login individual do aplicativo é uma camada adicional.

1. Instale e teste o monitor localmente, incluindo o primeiro administrador.
2. Conecte o Tailscale no escritório e nos dispositivos autorizados.
3. Consulte `tailscale ip -4` no escritório. Salve `remote-access.json` junto ao servidor, usando o endereço real:

```json
{"tailscale_ipv4": "100.64.0.10"}
```

4. Execute no escritório:

```powershell
& "$env:ProgramFiles\Tailscale\tailscale.exe" serve --bg --tcp=8130 tcp://127.0.0.1:8130
```

5. Acesse `http://IP-TAILSCALE:8130` de um dispositivo autorizado. O tráfego desse acesso HTTP passa pelo túnel criptografado Tailscale. Não use esse HTTP em uma rede pública sem o túnel.

O aplicativo consulta as câmeras e a telemetria pelo servidor. Links Mainsail/Creality apontam aos IPs privados e exigem rotas Tailscale separadas. Restrinja essas rotas aos operadores autorizados: permitir a rede das impressoras dá acesso a suas interfaces fora das permissões deste painel. Não abra portas do roteador para essas interfaces.

O computador precisa estar ligado, sem suspender, e o processo do monitor precisa permanecer em execução. Os scripts deste repositório não instalam serviço de sistema nem garantem reinício automático depois de falha. Para acesso permanente, será necessário configurar e testar a inicialização no computador definitivo.

## Link público HTTPS

Esta implantação ainda precisa ser configurada e validada no ambiente escolhido. Não aponte um site estático aos IPs locais e não exponha diretamente `http.server` à internet.

Uma implantação pública precisa de:

- Domínio e certificado HTTPS, com proxy reverso/túnel autenticado de origem até o computador do escritório, ou um gateway próprio entre hospedagem e escritório.
- Processo supervisionado e banco persistente, backups e limites de conexões/tamanho/tempo no proxy.
- `IMPACTO_PUBLIC_ORIGIN=https://monitor.seudominio.com` no ambiente do servidor. Sem caminho ou barra final. O proxy deve preservar esse Host e encaminhar ao loopback. O aplicativo rejeita outras origens de POST e marca os cookies dessa origem com Secure.
- Testes de login, permissões, revogação e câmeras a partir de outra internet. Validar limites de login atrás do proxy: o aplicativo usa o IP da conexão, não confia em X-Forwarded-For. Vários usuários podem compartilhar esse limite.

O backend atual usa `http.server` da biblioteca padrão. Antes de uma exposição pública para uso contínuo, migre a borda HTTP para um servidor de aplicação adequado, valide TLS/proxy e trate recuperação de contas, MFA e retenção de logs conforme o uso. Este repositório não contém uma implantação pública pronta.

Mainsail/Creality em HTTP privado não podem ser incorporados normalmente em uma página HTTPS pública. O mosaico de imagens servido pelo próprio backend funciona sem esse link direto; controle externo das interfaces requer VPN ou integração adicional.

Referências oficiais:

- [GitHub Pages: limitações de servidor](https://docs.github.com/en/pages/getting-started-with-github-pages/creating-a-github-pages-site)
- [Tailscale Serve](https://tailscale.com/docs/reference/tailscale-cli/serve)
- [Rotas no Windows](https://tailscale.com/docs/features/subnet-routers/how-to/setup?tab=windows)
- [Python http.server](https://docs.python.org/3/library/http.server.html)
