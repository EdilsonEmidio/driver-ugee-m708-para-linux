#!/usr/bin/env bash
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "=== Instalador do Driver + Interface Gráfica Ugee M708 para Linux (Debian 13 Wayland / GNOME) ==="
echo ""

# 1. Verifica dependências
echo "[1/7] Verificando dependências do sistema..."
MISSING_PKGS=()
if ! python3 -c "import evdev" &>/dev/null; then
    MISSING_PKGS+=("python3-evdev")
fi
if ! python3 -c "import gi; gi.require_version('Gtk', '4.0'); gi.require_version('Adw', '1')" &>/dev/null; then
    MISSING_PKGS+=("python3-gi" "python3-gi-cairo" "gir1.2-gtk-4.0" "gir1.2-adw-1")
fi

if [ ${#MISSING_PKGS[@]} -gt 0 ]; then
    echo "Instalando pacotes necessários (${MISSING_PKGS[*]}) via apt..."
    sudo apt-get update && sudo apt-get install -y "${MISSING_PKGS[@]}"
else
    echo "Todas as dependências Python/GTK4/Libadwaita/evdev já estão instaladas."
fi

# 2. Copia os scripts executáveis e assets
echo "[2/7] Instalando executáveis e recursos gráficos..."
sudo cp "${SCRIPT_DIR}/ugee_m708_driver.py" /usr/local/bin/ugee_m708_driver.py
sudo cp "${SCRIPT_DIR}/ugee_m708_gui.py" /usr/local/bin/ugee_m708_gui.py
sudo chmod +x /usr/local/bin/ugee_m708_driver.py /usr/local/bin/ugee_m708_gui.py

sudo mkdir -p /usr/local/share/ugee-m708/assets
if [ -d "${SCRIPT_DIR}/assets" ]; then
    sudo cp -r "${SCRIPT_DIR}/assets/"* /usr/local/share/ugee-m708/assets/
fi

# 3. Instala ícone e atalho no Menu Iniciar (GNOME Applications)
echo "[3/7] Instalando ícone e atalho no menu de aplicativos do GNOME..."
for sz in 16 32 48 64 128 256 512; do
    mkdir -p "${HOME}/.local/share/icons/hicolor/${sz}x${sz}/apps"
    if [ -f "${SCRIPT_DIR}/assets/icons/${sz}x${sz}/ugee-m708.png" ]; then
        cp "${SCRIPT_DIR}/assets/icons/${sz}x${sz}/ugee-m708.png" "${HOME}/.local/share/icons/hicolor/${sz}x${sz}/apps/ugee-m708.png"
        sudo mkdir -p "/usr/share/icons/hicolor/${sz}x${sz}/apps"
        sudo cp "${SCRIPT_DIR}/assets/icons/${sz}x${sz}/ugee-m708.png" "/usr/share/icons/hicolor/${sz}x${sz}/apps/ugee-m708.png"
    fi
done

mkdir -p "${HOME}/.local/share/pixmaps"
if [ -f "${SCRIPT_DIR}/assets/icon.png" ]; then
    cp "${SCRIPT_DIR}/assets/icon.png" "${HOME}/.local/share/pixmaps/ugee-m708.png"
    sudo mkdir -p /usr/share/pixmaps
    sudo cp "${SCRIPT_DIR}/assets/icon.png" /usr/share/pixmaps/ugee-m708.png
fi

mkdir -p "${HOME}/.local/share/applications"
sed "s|Exec=.*|Exec=/usr/local/bin/ugee_m708_gui.py|g" "${SCRIPT_DIR}/ugee-m708.desktop" > "${HOME}/.local/share/applications/ugee-m708.desktop"
chmod +x "${HOME}/.local/share/applications/ugee-m708.desktop"
sudo cp "${HOME}/.local/share/applications/ugee-m708.desktop" /usr/share/applications/ugee-m708.desktop
gtk-update-icon-cache -f -t "${HOME}/.local/share/icons/hicolor" 2>/dev/null || true
sudo gtk-update-icon-cache -f -t /usr/share/icons/hicolor 2>/dev/null || true
update-desktop-database "${HOME}/.local/share/applications" 2>/dev/null || true
sudo update-desktop-database /usr/share/applications 2>/dev/null || true

# 4. Instala as regras prioritárias do udev (< 73 para executar antes de 73-seat-late)
echo "[4/7] Configurando regras udev em /etc/udev/rules.d/60-ugee-m708.rules..."
sudo rm -f /etc/udev/rules.d/99-ugee-m708.rules
sudo cp "${SCRIPT_DIR}/60-ugee-m708.rules" /etc/udev/rules.d/60-ugee-m708.rules
sudo udevadm control --reload-rules
sudo udevadm trigger

# 5. Adiciona o usuário atual ao grupo 'input' se necessário
if ! id -nG "$USER" | grep -qw "input"; then
    echo "Adicionando o usuário '$USER' ao grupo 'input'..."
    sudo usermod -aG input "$USER"
fi

# 6. Instala a definição do libwacom para o GNOME Wayland
echo "[5/7] Instalando definição libwacom em /etc/libwacom/ugee-m708.tablet..."
sudo mkdir -p /etc/libwacom
sudo cp "${SCRIPT_DIR}/ugee-m708.tablet" /etc/libwacom/ugee-m708.tablet

# 7. Configura o serviço systemd de usuário
echo "[6/7] Configurando e reiniciando serviço systemd de usuário..."
USER_SYSTEMD_DIR="${HOME}/.config/systemd/user"
mkdir -p "${USER_SYSTEMD_DIR}"
cp "${SCRIPT_DIR}/ugee-m708.service" "${USER_SYSTEMD_DIR}/ugee-m708.service"

systemctl --user daemon-reload
systemctl --user enable ugee-m708.service
systemctl --user restart ugee-m708.service

echo "[7/7] Verificando status do serviço..."
systemctl --user status ugee-m708.service --no-pager || true

echo ""
echo "=== Instalação concluída com sucesso! ==="
echo "• Você já pode abrir 'Mesa Digitalizadora Ugee M708' pelo Menu Iniciar do GNOME (ou executando 'ugee_m708_gui.py')."
echo "• Para verificar os logs do driver em tempo real: journalctl --user -u ugee-m708.service -f"
