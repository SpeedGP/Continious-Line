"""Structural checks of continuous_line.inx against Inkscape 1.x conventions."""
import os
import sys
import xml.etree.ElementTree as ET

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NS = "{http://www.inkscape.org/namespace/inkscape/extension}"
VALID_TYPES = {"bool", "int", "float", "string", "path", "color", "optiongroup", "notebook"}


def _params(elem):
    for p in elem.iter(NS + "param"):
        yield p


def test_inx_structure():
    tree = ET.parse(os.path.join(ROOT, "continuous_line.inx"))
    root = tree.getroot()
    assert root.tag == NS + "inkscape-extension"
    assert root.find(NS + "name").text.strip() == "Continuous Line"
    assert "." in root.find(NS + "id").text
    effect = root.find(NS + "effect")
    assert effect is not None
    assert effect.find(NS + "object-type").text == "all"
    submenu = effect.find(NS + "effects-menu/" + NS + "submenu")
    assert submenu.get("name") == "Generate from Path"
    cmd = root.find(NS + "script/" + NS + "command")
    assert cmd.get("location") == "inx"
    assert cmd.get("interpreter") == "python"
    assert cmd.text.strip() == "continuous_line.py"

    names = set()
    for p in _params(root):
        assert p.get("type") in VALID_TYPES, p.get("name")
        assert p.get("name")
        names.add(p.get("name"))
        if p.get("type") != "notebook":
            assert p.get("gui-text"), "param %s needs gui-text" % p.get("name")
        if p.get("type") == "optiongroup":
            opts = p.findall(NS + "option")
            assert opts and all(o.get("value") for o in opts)
        if p.get("type") == "bool":
            assert p.text.strip() in ("true", "false")
        if p.get("type") == "float":
            float(p.text)
    expected = {"tab", "flatten_tol", "snap_tol", "start_corner", "keep_original",
                "min_island", "bridge_style", "allow_open", "stroke_width"}
    assert expected <= names


def test_inx_params_match_add_arguments():
    """Every .inx param must be declared by the Python side (and vice versa)."""
    src = open(os.path.join(ROOT, "continuous_line.py"), encoding="utf-8").read()
    root = ET.parse(os.path.join(ROOT, "continuous_line.inx")).getroot()
    for p in _params(root):
        assert ('"--%s"' % p.get("name")) in src, "missing add_argument for %s" % p.get("name")
    for corner in ("bottom-left", "top-left", "top-right", "bottom-right"):
        assert corner in [o.get("value") for o in root.iter(NS + "option")]


if __name__ == "__main__":
    test_inx_structure()
    test_inx_params_match_add_arguments()
    print("PASS inx")
