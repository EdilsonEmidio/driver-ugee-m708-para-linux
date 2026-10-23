#!/usr/bin/env bash
set -e

echo "=== Desinstalador do Driver + Interface Gráfica Ugee M708 ==="

systemctl --user stop ugee-m708.service 2>/dev/null || true
systemctl --user disable ugee-m708.service 2>/dev/null || true
rm -f "${HOME}/.config/systemd/user/ugee-m708.service"
systemctl --user daemon-reload

for sz in 16 32 48 64 128 256 512; do
    rm -f "${HOME}/.local/share/icons/hicolor/${sz}x${sz}/apps/ugee-m708.png"
    sudo rm -f "/usr/share/icons/hicolor/${sz}x${sz}/apps/ugee-m708.png"
done
rm -f "${HOME}/.local/share/pixmaps/ugee-m708.png"
sudo rm -f /usr/share/pixmaps/ugee-m708.png

rm -f "${HOME}/.local/share/applications/ugee-m708.desktop"
sudo rm -f /usr/share/applications/ugee-m708.desktop
gtk-update-icon-cache -f -t "${HOME}/.local/share/icons/hicolor" 2>/dev/null || true
sudo gtk-update-icon-cache -f -t /usr/share/icons/hicolor 2>/dev/null || true
update-desktop-database "${HOME}/.local/share/applications" 2>/dev/null || true
sudo update-desktop-database /usr/share/applications 2>/dev/null || true

sudo rm -f /usr/local/bin/ugee_m708_driver.py /usr/local/bin/ugee_m708_gui.py
sudo rm -rf /usr/local/share/ugee-m708
sudo rm -f /etc/udev/rules.d/60-ugee-m708.rules /etc/udev/rules.d/99-ugee-m708.rules
sudo rm -f /etc/libwacom/ugee-m708.tablet
sudo udevadm control --reload-rules
sudo udevadm trigger

echo "Driver e interface gráfica desinstalados com sucesso!"
