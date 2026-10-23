# Driver + Interface Gráfica para Mesa Digitalizadora Ugee M708 no Linux (Wayland / GNOME)

[![Linux](https://img.shields.io/badge/Linux-Debian%20%7C%20Ubuntu%20%7C%20Fedora%20%7C%20Arch-blue.svg)](#)
[![Display](https://img.shields.io/badge/Display-Wayland%20%7C%20X11-green.svg)](#)
[![UI](https://img.shields.io/badge/GUI-GTK%204%20%2B%20Libadwaita-orange.svg)](#)

Driver user-space completo e painel de controle nativo em **GTK 4 / Libadwaita** para a mesa digitalizadora **Ugee M708** no Linux. Desenvolvido para funcionar com suporte total ao **Wayland** (GNOME / Mutter), compatível com revisões V1, V2 e V3.

---

## 🎨 Recursos Principais

1. **Interface Gráfica Nativa (GTK4 + Libadwaita)**:
   - Integrada ao visual do GNOME com atalho e ícone oficial da Ugee no Menu Iniciar.
   - Comunicação bidirecional de baixa latência em tempo real via Unix Domain Socket IPC (`ugee-m708.sock`).

2. **Área de Trabalho e Rotação**:
   - Ajuste interativo da **Área Ativa na Mesa** (redimensionamento pelos 4 cantos/bordas e controles percentuais).
   - Ajuste da **Região de Mapeamento na Tela** (Monitor inteiro, metade esquerda/direita, centralizado).
   - Indicador verde em tempo real sobre a ilustração da mesa mostrando a posição física da caneta.
   - Suporte a rotações: **0°** (Padrão), **90°**, **180°** (Modo Canhoto) e **270°**.
   - **Modo de Precisão (JingXiMode)**: reduz a velocidade e foca no centro para traços finos.

3. **Caneta e Sensibilidade à Pressão**:
   - Editor gráfico interativo da **Curva de Pressão Bézier quadrática ($P_0, P_1, P_2$)** com presets rápidos (*Muito Macia*, *Macia*, *Linear*, *Firme*, *Muito Firme*).
   - Barra de nível de pressão e **Quadro de Teste de Desenho** com resposta de espessura instantânea de $0.6\text{ px}$ a $24\text{ px}$.
   - Mapeamento customizado dos 2 botões laterais da caneta (Clique Direito, Clique do Meio, Alternar Borracha, Desfazer Ctrl+Z, Mão/Espaço, etc.).

4. **Teclas de Atalho Físicas (Express Keys K1–K8)**:
   - Dispositivo virtual `uinput` dedicado para disparar qualquer atalho de teclado global no Wayland (`Ctrl+Z`, `B`, `E`, `Space`, `Scroll Up/Down`, etc.) ou comandos arbitrários.
   - Notificações na tela (OSD via `notify-send`) ao pressionar botões físicos.
   - Indicadores visuais que acendem ao vivo conforme você pressiona os botões físicos.
   - **Painel de Atalhos Flutuante**: botões virtuais na tela para acionamento por clique.

5. **Serviço de Inicialização Automática**:
   - Gerenciado como serviço de usuário do systemd (`ugee-m708.service`), iniciando automaticamente com a sessão gráfica.

---

## 💻 Compatibilidade de Hardware

| Modelo | Vendor ID | Product ID | Níveis de Pressão |
| :--- | :--- | :--- | :--- |
| **Ugee M708 v1/v2** | `0x28BD` | `0x0907` | 8192 níveis |
| **Ugee M708 v2** | `0x28BD` | `0x0924` | 8192 níveis |
| **Ugee M708 v3** | `0x28BD` | `0x0927` | 16384 níveis |

---

## 🚀 Instalação Rápida

Em qualquer computador com Debian, Ubuntu ou derivados:

```bash
# 1. Clone o repositório
git clone https://github.com/SEU-USUARIO/ugee-driver.git
cd ugee-driver

# 2. Execute o instalador automatizado
chmod +x install.sh
./install.sh
```

O script cuidará de:
* Instalar pacotes necessários (`python3-evdev`, `python3-gi`, `python3-gi-cairo`, `gir1.2-gtk-4.0`, `gir1.2-adw-1`).
* Copiar executáveis para `/usr/local/bin/`.
* Instalar o ícone da Ugee em todas as resoluções e o atalho `.desktop` no Menu Iniciar.
* Configurar permissões `udev` em `/etc/udev/rules.d/99-ugee-m708.rules`.
* Instalar a definição `libwacom` em `/etc/libwacom/ugee-m708.tablet`.
* Habilitar e iniciar o serviço `systemd --user ugee-m708.service`.

> **Nota sobre outros drivers:** Caso tenha o `OpenTabletDriver` instalado, lembre-se de desativá-lo para evitar conflito de acesso ao dispositivo:
> ```bash
> systemctl --user stop opentabletdriver.service 2>/dev/null || true
> systemctl --user disable opentabletdriver.service 2>/dev/null || true
> ```

---

## 🖥️ Como Abrir o Painel

* **Pelo Menu de Aplicativos:** Pressione a tecla `Super` (Windows) e busque por **"Mesa Digitalizadora Ugee M708"**.
* **Pelo Terminal:**
  ```bash
  ugee_m708_gui.py
  ```

---

## 🗑️ Desinstalação

Para remover completamente o driver e a interface gráfica do sistema:

```bash
chmod +x uninstall.sh
./uninstall.sh
```

---

## 📄 Licença

Distribuído sob a licença MIT. Imagens e recursos visuais extraídos são propriedade da Hanvon Ugee Technology Co., Ltd.
