# Goldfish

Puff is a photoreal goldfish that swims across the desktop.

He is about 4 cm long. His nose follows the way he is swimming and his belly stays down. Click him and he darts. Right-click him for a snack, or to quit.

Each heading is a photograph. The other side is a mirror. The tail flaps from that picture. He swims the whole work area, over the desktop, rather than inside a tank.

## Install

Needs Python 3, GTK 3, PyGObject, cairo, Pillow, NumPy, and OpenCV (`python3-gi`, `python3-gi-cairo`, `gir1.2-gtk-3.0`, `python3-pil`, `python3-numpy`, `python3-opencv`). A C compiler builds the small helper used by the self-test. The desktop uses XWayland so he can keep his place.

```bash
git clone https://github.com/burnt1983/goldfish.git
cd goldfish
bash install.sh
puff
```

The pictures unpack into `~/.local/share/goldfish/fish/`. Place and mood are saved in `~/.config/goldfish/config.json`. It does not start at login.

## How to use it

| Action | What happens |
|---|---|
| Watch | He swims across the desktop |
| Click him | He darts away |
| Right-click, Food | A snack |
| Right-click, Quit | Take him off the desktop |
| Run `puff` again | The fish that is already swimming darts |

## Uninstall

```bash
bash uninstall.sh
```

## Licence

MIT. The goldfish pictures are part of this project. See [LICENSE](LICENSE).
