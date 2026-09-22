"""
Service USB pour imprimantes thermiques ESC/POS (ex. Epson TM-T20III).

- scan_usb_printers() : détecte les imprimantes (classe USB 0x07).
- print_test_page(vid, pid) : page de test.
- print_receipt(commande, panier_items, vid, pid) : reçu de caisse après paiement.

Windows : associer le pilote WinUSB via Zadig (PAS « USB Serial CDC »).
VID/PID par défaut Epson TM-T20III : 0x04B8 / 0x0E28.
"""
from __future__ import annotations

import importlib
import os
import textwrap
import time
from contextlib import contextmanager
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from types import ModuleType

try:
    import usb.core
    import usb.util
    HAS_USB = True
except ImportError:
    usb = None
    HAS_USB = False

libusb_package: ModuleType | None = None
try:
    libusb_package = importlib.import_module("libusb_package")
except ImportError:
    pass

DEFAULT_VID = 0x04B8
DEFAULT_PID = 0x0E28
USB_PRINTER_CLASS = 0x07
_BUNDLED_LIBUSB = Path(__file__).resolve().parent / "libusb" / "libusb-1.0.dll"
_backend_cache = None


def _bundled_libusb_path() -> str | None:
    if _BUNDLED_LIBUSB.is_file():
        return str(_BUNDLED_LIBUSB)
    return None


def _get_backend():
    """Backend libusb1 (mis en cache)."""
    global _backend_cache
    if not HAS_USB:
        return None
    if _backend_cache is not None:
        return _backend_cache

    import usb.backend.libusb1 as libusb1

    if libusb_package is not None:
        try:
            fn = getattr(libusb_package, "get_libusb1_backend", None)
            if callable(fn):
                backend = fn()
                if backend is not None:
                    _backend_cache = backend
                    return backend
        except Exception:
            pass
        try:
            backend = libusb1.get_backend(find_library=libusb_package.find_library)
            if backend is not None:
                _backend_cache = backend
                return backend
        except Exception:
            pass

    bundled = _bundled_libusb_path()
    if bundled:
        pkg_dir = str(_BUNDLED_LIBUSB.parent)
        if pkg_dir not in os.environ.get("PATH", ""):
            os.environ["PATH"] = pkg_dir + os.pathsep + os.environ.get("PATH", "")

        def _find_bundled(_name: str) -> str | None:
            return bundled

        try:
            backend = libusb1.get_backend(find_library=_find_bundled)
            if backend is not None:
                _backend_cache = backend
                return backend
        except Exception:
            pass

    try:
        backend = libusb1.get_backend()
        if backend is not None:
            _backend_cache = backend
            return backend
    except Exception:
        pass

    return None


def backend_status() -> dict:
    return {
        "has_pyusb": HAS_USB,
        "has_libusb_package": libusb_package is not None,
        "bundled_dll": _bundled_libusb_path(),
        "backend_ok": _get_backend() is not None,
    }


def _require_backend():
    if not HAS_USB:
        raise RuntimeError(
            "Support USB indisponible. Installez : pip install pyusb libusb-package"
        )
    backend = _get_backend()
    if backend is None:
        raise RuntimeError(
            "Backend libusb introuvable. Réinstallez les dépendances "
            "(pip install -r requirements.txt). "
            "Sur Windows, utilisez Zadig avec WinUSB (pas USB Serial CDC)."
        )
    return backend


def _safe_get_string(dev, index):
    if not index:
        return ""
    try:
        return usb.util.get_string(dev, index) or ""
    except Exception:
        return ""


def _is_printer_device(dev):
    try:
        if dev.bDeviceClass == USB_PRINTER_CLASS:
            return True
        for cfg in dev:
            for intf in cfg:
                if intf.bInterfaceClass == USB_PRINTER_CLASS:
                    return True
    except Exception:
        pass
    return False


def _device_to_dict(dev):
    info = {
        "vid": dev.idVendor,
        "pid": dev.idProduct,
        "vid_hex": f"0x{dev.idVendor:04X}",
        "pid_hex": f"0x{dev.idProduct:04X}",
        "manufacturer": _safe_get_string(dev, dev.iManufacturer),
        "product": _safe_get_string(dev, dev.iProduct),
        "serial": _safe_get_string(dev, dev.iSerialNumber),
        "bus": getattr(dev, "bus", None),
        "address": getattr(dev, "address", None),
        "accessible": True,
    }
    if not info["manufacturer"] and not info["product"]:
        info["accessible"] = False
        info["product"] = "Imprimante détectée (pilote WinUSB requis via Zadig)"
    return info


def scan_usb_printers():
    backend = _require_backend()
    devices = list(usb.core.find(find_all=True, backend=backend))
    return [_device_to_dict(dev) for dev in devices if _is_printer_device(dev)]


def get_printer_info(vid, pid):
    if not HAS_USB:
        return None
    backend = _get_backend()
    if backend is None:
        return None
    dev = usb.core.find(idVendor=vid, idProduct=pid, backend=backend)
    if dev is None:
        return None
    return _device_to_dict(dev)


def resolve_vid_pid(vid=None, pid=None):
    v = int(vid) if vid is not None else DEFAULT_VID
    p = int(pid) if pid is not None else DEFAULT_PID
    return v, p


def _find_printer_endpoint(dev):
    cfg = dev.get_active_configuration()
    for intf in cfg:
        if intf.bInterfaceClass != USB_PRINTER_CLASS:
            continue
        for ep in intf:
            if usb.util.endpoint_direction(ep.bEndpointAddress) == usb.util.ENDPOINT_OUT:
                return ep.bEndpointAddress
    return 0x01


class EscPosWriter:
    def __init__(self, dev, ep_out: int, margin: int = 6, line_width: int = 50):
        self.dev = dev
        self.ep_out = ep_out
        self.margin = margin
        self.line_width = line_width
        self.inner = line_width - margin * 2

    def raw(self, data: bytes):
        self.dev.write(self.ep_out, data)

    def write(self, text: str, align: str = "left", bold: bool = False, double: bool = False):
        alignments = {
            "left": b"\x1b\x61\x00",
            "center": b"\x1b\x61\x01",
            "right": b"\x1b\x61\x02",
        }
        self.raw(alignments.get(align, alignments["left"]))
        self.raw(b"\x1b\x45" + (b"\x01" if bold else b"\x00"))
        self.raw(b"\x1d\x21" + (b"\x11" if double else b"\x00"))
        padded = f"{' ' * self.margin}{text}{' ' * self.margin}"
        self.raw(f"{padded[: self.line_width]}\n".encode("cp850", errors="replace"))
        self.raw(b"\x1b\x21\x00")

    def feed_cut(self, feed_lines: int = 10):
        for _ in range(feed_lines):
            self.raw(b"\x0a")
        try:
            self.raw(b"\x1d\x56\x00")
        except Exception:
            pass


class _BufferDevice:
    """Cible d'ecriture compatible avec EscPosWriter, qui accumule en memoire.

    EscPosWriter.raw() appelle self.dev.write(endpoint, data) : il suffit donc
    d'un objet exposant la meme methode pour produire le flux ESC/POS sans
    aucun peripherique. C'est ce qui permet au serveur de composer le ticket et
    de le laisser envoyer par le navigateur du poste de caisse (WebUSB), la ou
    l'imprimante est reellement branchee.
    """

    def __init__(self):
        self.buffer = bytearray()

    def write(self, endpoint, data):  # signature imposee par EscPosWriter
        self.buffer.extend(data)
        return len(data)


def _new_buffer_writer() -> tuple[_BufferDevice, EscPosWriter]:
    """Prepare un writer memoire, initialise comme une session USB reelle."""
    sink = _BufferDevice()
    writer = EscPosWriter(sink, ep_out=0)
    writer.raw(b"\x1b\x40")  # ESC @ : reinitialisation, comme _usb_printer_session
    return sink, writer


@contextmanager
def _usb_printer_session(vid, pid):
    backend = _require_backend()
    dev = usb.core.find(idVendor=vid, idProduct=pid, backend=backend)
    if dev is None:
        raise RuntimeError(
            f"Imprimante VID=0x{vid:04X} PID=0x{pid:04X} introuvable. "
            "Branchez l'USB et vérifiez WinUSB dans Zadig."
        )

    kernel_detached = False
    try:
        try:
            if dev.is_kernel_driver_active(0):
                dev.detach_kernel_driver(0)
                kernel_detached = True
        except (usb.core.USBError, NotImplementedError, AttributeError):
            pass
        try:
            dev.set_configuration()
        except usb.core.USBError as e:
            if getattr(e, "errno", None) == 13:
                raise RuntimeError(
                    "Accès USB refusé. Dans Zadig, installez WinUSB (pas USB Serial CDC), "
                    "puis débranchez/rebranchez l'imprimante."
                ) from e
            raise RuntimeError(f"Configuration USB : {e}") from e

        ep_out = _find_printer_endpoint(dev)
        dev.write(ep_out, b"\x1b\x40")
        yield EscPosWriter(dev, ep_out)
    finally:
        if dev is not None:
            try:
                try:
                    usb.util.release_interface(dev, 0)
                except (usb.core.USBError, AttributeError):
                    pass
                if kernel_detached:
                    try:
                        dev.attach_kernel_driver(0)
                    except (usb.core.USBError, NotImplementedError, AttributeError):
                        pass
                try:
                    dev.reset()
                except (usb.core.USBError, AttributeError):
                    pass
                time.sleep(0.2)
            except Exception:
                pass


def _compose_test_page(w, vid, pid):
    """Compose la page de test dans un writer, USB reel ou tampon memoire."""
    w.write("P&B Auto-Pieces", align="center", bold=True, double=True)
    w.write("=" * w.inner, align="center")
    w.write("PAGE DE TEST", align="center", bold=True)
    w.write("=" * w.inner, align="center")
    w.write(f"VID  : 0x{vid:04X}", align="left")
    w.write(f"PID  : 0x{pid:04X}", align="left")
    w.write(f"Date : {datetime.now().strftime('%d/%m/%Y %H:%M:%S')}", align="left")
    w.write("-" * w.inner, align="center")
    w.write("Si vous lisez ce ticket,", align="center")
    w.write("l'imprimante est connectee.", align="center")
    w.write("=" * w.inner, align="center")
    w.feed_cut(6)


def print_test_page(vid, pid):
    vid, pid = resolve_vid_pid(vid, pid)
    with _usb_printer_session(vid, pid) as w:
        _compose_test_page(w, vid, pid)
    time.sleep(0.4)


def build_test_page_bytes(vid=None, pid=None) -> bytes:
    """Page de test au format ESC/POS, a envoyer par le navigateur (WebUSB)."""
    vid, pid = resolve_vid_pid(vid, pid)
    sink, writer = _new_buffer_writer()
    _compose_test_page(writer, vid, pid)
    return bytes(sink.buffer)


def _compose_receipt(w, commande, panier_items):
    """Compose le recu de caisse dans un writer, USB reel ou tampon memoire.

    La mise en page vient de stock.receipt_layout, partagee avec le PDF : il n'y
    a qu'une seule definition du ticket, quel que soit le canal d'impression.
    """
    from stock.receipt_layout import (
        build_receipt_item_rows,
        caissier_label,
        commande_date_label,
        remise_line,
        spaced_line,
        total_line,
        RECEIPT_FOOTER_LINES,
    )

    caissier = caissier_label(commande)

    w.write("P&B Auto-Pieces", align="center", bold=True, double=True)
    w.write("*" * w.inner, align="center")
    w.write("REÇU DE CAISSE", align="center", bold=True)
    w.write("*" * w.inner, align="center")

    commande_label = f"{commande.numero_commande}"
    date_label = commande_date_label(commande)
    w.write(spaced_line(commande_label, date_label, w.inner), align="left")
    w.write(f"Caissier : {caissier}", align="left")
    w.write("-" * w.inner, align="center")

    w.write(f"{'Désignation':<20}{'Qte':>4}{'PU':>7}{'Tot':>7}", align="left", bold=True)
    for kind, *rest in build_receipt_item_rows(panier_items, commande):
        if kind == "row":
            desig, qte, pu, tot = rest
            w.write(f"{desig:<20}{qte:>4}{pu:>7}{tot:>7}", align="left")
        else:
            w.write(rest[0], align="left")

    w.write("-" * w.inner, align="center")
    w.raw(b"\x1b\x33\x14")

    w.write(total_line("Total Brut", commande.total_sans_remise, w.inner), align="left")
    rl = remise_line(commande, w.inner)
    if rl:
        w.write(rl, align="left")
    w.write(total_line("Total Net", commande.total, w.inner), align="left")
    tva = Decimal(str(commande.montant_tva or 0))
    if tva > 0:
        w.write(total_line("TVA", tva, w.inner), align="left")
    timbre = Decimal(str(commande.montant_timbre or 0))
    if timbre > 0:
        w.write(total_line("Timbre fiscal", timbre, w.inner), align="left")
    if tva > 0 or timbre > 0:
        w.write(total_line("A PAYER", commande.total_a_encaisser, w.inner), align="left")
    w.write(total_line("Payé", commande.montant_paye, w.inner), align="left")
    w.write(total_line("Rendu", commande.montant_reste, w.inner), align="left")

    moyen = getattr(commande, "libelle_paiement", None) or "—"
    w.write(spaced_line("Payer par", moyen, w.inner), align="left")

    w.raw(b"\x1b\x33\x1e")
    w.write("-" * w.inner, align="center")
    for line in RECEIPT_FOOTER_LINES:
        w.raw(f"\n{line}\n".encode("cp850", errors="replace"))
    w.feed_cut(10)


def print_receipt(commande, panier_items, vid=None, pid=None):
    """
    Imprime le reçu de caisse après validation du paiement.
    """
    vid, pid = resolve_vid_pid(vid, pid)
    with _usb_printer_session(vid, pid) as w:
        _compose_receipt(w, commande, panier_items)
    time.sleep(0.5)


def build_receipt_bytes(commande, panier_items) -> bytes:
    """Recu de caisse au format ESC/POS, a envoyer par le navigateur (WebUSB).

    Aucun peripherique n'est touche : le serveur ne fait que composer les
    octets, que le poste de caisse pousse ensuite vers son imprimante.
    """
    sink, writer = _new_buffer_writer()
    _compose_receipt(writer, commande, panier_items)
    return bytes(sink.buffer)


def _compose_order_slip(w, commande, panier_items):
    """Compose le BON DE COMMANDE (avant paiement) dans un writer.

    Remis au client pour qu'il se presente en caisse avec son numero de ticket.
    Aucune ligne de paiement ni de rendu : ce bon n'est pas un recu.

    La mise en page vient de stock.receipt_layout, comme le recu et le PDF :
    une seule definition des prix, des remises et du decoupage des libelles.
    """
    from stock.receipt_layout import (
        build_receipt_item_rows,
        commande_date_label,
        panier_localite,
        remise_line,
        spaced_line,
        total_line,
    )

    w.write("P&B Auto-Pieces", align="center", bold=True, double=True)
    w.write("*" * w.inner, align="center")
    w.write("BON DE COMMANDE", align="center", bold=True)
    w.write("(A presenter en caisse)", align="center")
    w.write("*" * w.inner, align="center")

    ticket_num = getattr(commande.panier, 'ticket', '') or f"TKT{commande.id}"
    w.write(f"Ticket : {ticket_num}", align="left", bold=True)
    w.write(
        spaced_line(f"{commande.numero_commande}", commande_date_label(commande), w.inner),
        align="left",
    )
    if commande.utilisateur:
        w.write(f"Hote : {commande.utilisateur.username}", align="left")
    local = panier_localite(commande)
    if local:
        w.write(f"Localite : {str(local)[:w.inner - 12]}", align="left")
    w.write("-" * w.inner, align="center")

    w.write(f"{'Désignation':<20}{'Qte':>4}{'PU':>7}{'Tot':>7}", align="left", bold=True)
    for kind, *rest in build_receipt_item_rows(panier_items, commande):
        if kind == "row":
            desig, qte, pu, tot = rest
            w.write(f"{desig:<20}{qte:>4}{pu:>7}{tot:>7}", align="left")
        else:
            w.write(rest[0], align="left")

    w.write("-" * w.inner, align="center")
    w.raw(b"\x1b\x33\x14")

    w.write(total_line("Total Brut", commande.total_sans_remise, w.inner), align="left")
    rl = remise_line(commande, w.inner)
    if rl:
        w.write(rl, align="left")
    w.write(
        spaced_line("A PAYER", f"{int(commande.total)} Fcfa", w.inner),
        align="left", bold=True,
    )

    w.raw(b"\x1b\x33\x1e")
    w.write("-" * w.inner, align="center")
    for ligne in (
        "Veuillez vous presenter a la caisse",
        "pour effectuer le paiement.",
        "Ce bon n'est pas un recu de paiement.",
        "*** P&B Auto-Pieces ***",
    ):
        w.raw(f"\n{ligne}\n".encode("cp850", errors="replace"))
    w.feed_cut(8)


def print_order_slip(commande, panier_items, vid=None, pid=None):
    """Imprime le bon de commande en USB direct (poste de developpement)."""
    vid, pid = resolve_vid_pid(vid, pid)
    with _usb_printer_session(vid, pid) as w:
        _compose_order_slip(w, commande, panier_items)
    time.sleep(0.5)


def build_order_slip_bytes(commande, panier_items) -> bytes:
    """Bon de commande au format ESC/POS, a envoyer par le navigateur (WebUSB)."""
    sink, writer = _new_buffer_writer()
    _compose_order_slip(writer, commande, panier_items)
    return bytes(sink.buffer)


def get_session_printer(request) -> dict | None:
    """Imprimante mémorisée en session Django."""
    data = request.session.get("usb_printer")
    if not data or "vid" not in data or "pid" not in data:
        return None
    return data


def set_session_printer(request, vid, pid, info: dict | None = None):
    request.session["usb_printer"] = {
        "vid": int(vid),
        "pid": int(pid),
        "vid_hex": f"0x{int(vid):04X}",
        "pid_hex": f"0x{int(pid):04X}",
        "product": (info or {}).get("product", ""),
        "manufacturer": (info or {}).get("manufacturer", ""),
    }
    request.session.modified = True


def clear_session_printer(request):
    request.session.pop("usb_printer", None)
    request.session.modified = True


def print_receipt_for_request(request, commande, panier_items):
    """
    Imprime en utilisant l'imprimante en session, sinon scan auto, sinon défaut Epson.
    """
    stored = get_session_printer(request)
    if stored:
        vid, pid = stored["vid"], stored["pid"]
    else:
        printers = scan_usb_printers()
        if printers:
            p = printers[0]
            vid, pid = p["vid"], p["pid"]
            set_session_printer(request, vid, pid, p)
        else:
            vid, pid = DEFAULT_VID, DEFAULT_PID
    print_receipt(commande, panier_items, vid=vid, pid=pid)
