"""Rekordbox XML (no grids, no cues), m3u8 and a printable tracklist."""
import html
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import quote


def _location_mac(path):
    return "file://localhost" + quote(str(path))


def _location_windows(path, project_root, windows_root):
    rel = Path(path).relative_to(project_root)
    base = windows_root.replace("\\", "/").rstrip("/") + "/" + project_root.name
    return "file://localhost/" + quote(f"{base}/{rel.as_posix()}", safe="/:")


def rekordbox_xml(project, tracks, playlists, out, windows_root=None):
    """tracks: enriched track dicts; playlists: [(name, [ids])]."""
    root = ET.Element("DJ_PLAYLISTS", Version="1.0.0")
    ET.SubElement(root, "PRODUCT", Name="rekordbox", Version="7", Company="AlphaTheta")
    col = ET.SubElement(root, "COLLECTION", Entries=str(len(tracks)))
    num = {}
    for n, t in enumerate(tracks, 1):
        num[t["id"]] = str(n)
        p = project.root / t["file"]
        title, artist = t["title"] or p.stem, t["artist"] or ""
        loc = (_location_windows(p, project.root, windows_root) if windows_root else _location_mac(p))
        attrs = {
            "TrackID": str(n), "Name": title, "Artist": artist, "Album": project.data["name"],
            "Genre": t.get("genre") or "", "Kind": t.get("kind") or "", "Size": str(p.stat().st_size),
            "TotalTime": str(int(t.get("duration") or 0)), "DateAdded": time.strftime("%Y-%m-%d"),
            "BitRate": str(t.get("bitrate") or 0), "SampleRate": str(t.get("sample_rate") or 44100),
            "Location": loc,
        }
        if t.get("bpm"):
            # reference only: no TEMPO / POSITION_MARK so rekordbox analyses grid, key and cues itself
            attrs["Comments"] = f"{t['bpm']:g} BPM - {t['camelot']}"
        ET.SubElement(col, "TRACK", attrs)
    pl = ET.SubElement(root, "PLAYLISTS")
    rootnode = ET.SubElement(pl, "NODE", Type="0", Name="ROOT", Count="1")
    folder = ET.SubElement(rootnode, "NODE", Type="0", Name=project.data["name"], Count=str(len(playlists)))
    for name, ids in playlists:
        node = ET.SubElement(folder, "NODE", Name=name, Type="1", KeyType="0", Entries=str(len(ids)))
        for i in ids:
            ET.SubElement(node, "TRACK", Key=num[i])
    ET.indent(root, "  ")
    ET.ElementTree(root).write(out, encoding="UTF-8", xml_declaration=True)


def m3u8(project, tracks_by_id, ids, out):
    with open(out, "w", encoding="utf-8") as f:
        f.write("#EXTM3U\n")
        for i in ids:
            t = tracks_by_id[i]
            f.write(f"#EXTINF:{int(t.get('duration') or 0)},{t['artist']} - {t['title']}\n{t['file']}\n")


def tracklist_html(project, tracks_by_id, blocks, out):
    rows = []
    n, start = 1, 0.0
    for b in blocks:
        rows.append(f"<tr class=b><td colspan=6>{html.escape(b['name'])}</td></tr>")
        for i in b["ids"]:
            t = tracks_by_id[i]
            m = int(start // 60)
            rows.append(
                f"<tr><td>{n}</td><td>{m // 60}:{m % 60:02d}</td><td>{html.escape(t['artist'])}</td>"
                f"<td>{html.escape(t['title'])}</td><td>{t.get('bpm', 0):g}</td><td>{t.get('camelot', '')}</td></tr>")
            n += 1
            start += t["play"]
    Path(out).write_text(f"""<!doctype html><meta charset=utf-8><title>{html.escape(project.data['name'])}</title>
<style>body{{font:13px -apple-system,sans-serif;margin:24px}}table{{border-collapse:collapse;width:100%}}
td{{padding:4px 8px;border-bottom:1px solid #ddd}}tr.b td{{background:#222;color:#fff;font-weight:600}}</style>
<h1>{html.escape(project.data['name'])}</h1><table><tr><th>#<th>Début<th>Artiste<th>Titre<th>BPM<th>Clé</tr>
{''.join(rows)}</table>""", encoding="utf-8")
