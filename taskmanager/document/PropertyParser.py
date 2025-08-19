from .WordAPI import *
from abc import ABC, abstractmethod
import re

vst = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
vst1 = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
vst2 = "{http://schemas.openxmlformats.org/markup-compatibility/2006}"

vsts = { 
    "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}": "w", 
    "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}": "r", 
    "{http://schemas.microsoft.com/office/word/2010/wordprocessingCanvas}": "wpc", 
    "{http://schemas.microsoft.com/office/drawing/2014/chartex}": "cx", 
    "{http://schemas.microsoft.com/office/drawing/2015/9/8/chartex}": "cx1", 
    "{http://schemas.microsoft.com/office/drawing/2015/10/21/chartex}": "cx2", 
    "{http://schemas.microsoft.com/office/drawing/2016/5/9/chartex}": "cx3", 
    "{http://schemas.microsoft.com/office/drawing/2016/5/10/chartex}": "cx4", 
    "{http://schemas.microsoft.com/office/drawing/2016/5/11/chartex}": "cx5", 
    "{http://schemas.microsoft.com/office/drawing/2016/5/12/chartex}": "cx6", 
    "{http://schemas.microsoft.com/office/drawing/2016/5/13/chartex}": "cx7", 
    "{http://schemas.microsoft.com/office/drawing/2016/5/14/chartex}": "cx8", 
    "{http://schemas.openxmlformats.org/markup-compatibility/2006}": "mc", 
    "{http://schemas.microsoft.com/office/drawing/2016/ink}": "aink", 
    "{http://schemas.microsoft.com/office/drawing/2017/model3d}": "am3d", 
    "{urn:schemas-microsoft-com:office:office}": "o", 
    "{http://schemas.microsoft.com/office/2019/extlst}": "oel", 
    "{http://schemas.microsoft.com/office/word/2010/wordprocessingDrawing}": "wp14", 
    "{http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing}": "wp", 
    "{urn:schemas-microsoft-com:vml}": "v", 
    "{http://schemas.microsoft.com/office/word/2010/wordml}": "w14", 
    "{http://schemas.microsoft.com/office/word/2012/wordml}": "w15", 
    "{http://schemas.microsoft.com/office/word/2018/wordml/cex}": "w16cex", 
    "{http://schemas.microsoft.com/office/word/2016/wordml/cid}": "w16cid", 
    "{http://schemas.microsoft.com/office/word/2018/wordml}": "w16", 
    "{http://schemas.microsoft.com/office/word/2023/wordml/word16du}": "w16du", 
    "{http://schemas.microsoft.com/office/word/2020/wordml/sdtdatahash}": "w16sdtdh", 
    "{http://schemas.microsoft.com/office/word/2015/wordml/symex}": "w16se", 
    "{http://schemas.microsoft.com/office/word/2010/wordprocessingGroup}": "wpg", 
    "{http://schemas.microsoft.com/office/word/2010/wordprocessingInk}": "wpi", 
    "{http://schemas.microsoft.com/office/word/2006/wordml}": "wne", 
    "{http://schemas.microsoft.com/office/word/2010/wordprocessingShape}": "wps" 
}
def get_attributes(attr: dict) -> dict:
    attrib = {}
    for k, v in attr.items():
        attrib[f"{vsts[k.split('}')[0] + '}']}:" + k.split('}')[1]] = v
    return attrib


def rgb_to_hex(rgb: tuple) -> str:
    return str('%02x%02x%02x' % rgb).upper()


def str_to_array(s: str) -> tuple:
    r = s.split(",")[0].split("(")[1]
    g = s.split(",")[1]
    b = s.split(",")[2].split(")")[0]
    return tuple((int(r), int(g), int(b)))


class Property(ABC):
    tag: str
    attrib: dict
    enabled: bool

    def __init__(self):
        self.tag = ""
        self.attrib = {}
        self.enabled = False

    def to_lxml(self):
        elem = create_element(self.tag)
        for k, v in self.attrib.items():
            create_attribute(elem, k, v)
        return elem

    @abstractmethod
    def to_css(self) -> str:
        pass

    @abstractmethod
    def from_json(self, json: dict):
        pass


class Align(Property):

    def __init__(self, elem=None):
        super().__init__()
        self.tag = "w:jc"
        if elem is not None:
            self.attrib = get_attributes(elem.attrib)
            self.enabled = True

    def to_css(self) -> str:
        css = """"""
        for k, v in self.attrib.items():
            if k == "w:val":
                css += f"text-align: {v if v != 'both' else 'justify'};\n"
        return css

    def from_json(self, json: dict):
        for k, v in json.items():
            if k == "textAlign":
                self.attrib["w:val"] = v if v != 'justify' else 'both'
                self.enabled = True


class Background(Property):

    def __init__(self, elem=None):
        super().__init__()
        self.tag = "w:shd"
        if elem is not None:
            self.attrib = get_attributes(elem.attrib)
            self.enabled = True

    def to_css(self) -> str:
        css = """"""
        for k, v in self.attrib.items():
            if k == "w:fill":
                f"background-color: #{v};\n"
        return css
    
    def convert_rgb_to_hex(self, rgb):
        match = re.match(r'^rgb\((\d+),\s*(\d+),\s*(\d+)\)$', rgb)
        if match:
            r, g, b = map(int, match.groups())
            return f'#{r:02x}{g:02x}{b:02x}'
        return rgb

    def from_json(self, json: dict):
        for k, v in json.items():
            if k == "backgroundColor":
                print(k, v)
                hexColor = self.convert_rgb_to_hex(v)
                print(k, hexColor)
                self.attrib['w:fill'] = hexColor
                self.enabled = True

class Highlight(Property):

    def __init__(self, elem=None):
        super().__init__()
        self.tag = "w:highlight"
        if elem is not None:
            self.attrib = get_attributes(elem.attrib)
            self.enabled = True

    def to_css(self) -> str:
        css = """"""
        for k, v in self.attrib.items():
            if k == "w:val":
                f"background-color: #{v};\n"
        return css
    
    def rgb_to_word_color(self, rgb):
        colors = {
            'rgb(255, 255, 255)': 'none',
            'rgb(255, 255, 0)': 'yellow',
            'rgb(0, 0, 0)': 'black',
            'rgb(0, 0, 255)': 'blue',
            'rgb(0, 255, 255)': 'cyan',
            'rgb(0, 128, 0)': 'green',
            'rgb(255, 0, 255)': 'magenta',
            'rgb(255, 0, 0)': 'red',
            'rgb(255, 255, 255)': 'white',
            'rgb(0, 0, 128)': 'darkBlue',
            'rgb(0, 128, 128)': 'darkCyan',
            'rgb(0, 128, 0)': 'darkGreen',
            'rgb(128, 0, 128)': 'darkMagenta',
            'rgb(128, 0, 0)': 'darkRed',
            'rgb(128, 128, 0)': 'darkYellow',
            'rgb(128, 128, 128)': 'darkGray',
            'rgb(192, 192, 192)': 'lightGray'
        }
        return colors.get(rgb, 'unknown')
    
    def convert_rgb_to_hex(self, rgb):
        match = re.match(r'^rgb\((\d+),\s*(\d+),\s*(\d+)\)$', rgb)
        if match:
            r, g, b = map(int, match.groups())
            return f'#{r:02x}{g:02x}{b:02x}'
        return rgb

    def from_json(self, json: dict):
        for k, v in json.items():
            if k == "backgroundColor":
                print(k, v)
                textColor = self.rgb_to_word_color(v)
                print(k, textColor)
                self.attrib['w:val'] = textColor
                self.enabled = True


class Spacing(Property):

    def __init__(self, elem=None):
        super().__init__()
        self.tag = "w:spacing"
        if elem is not None:
            self.attrib = get_attributes(elem.attrib)
            self.enabled = True

    def to_css(self) -> str:
        css = """"""
        for k, v in self.attrib.items():
            if k == "w:line":
                css += f"line-height: {float(v) / 240};\n"
            elif k == "w:after":  # Отступ до параграфа
                css += f"margin-bottom: {float(v) / 240}em;\n"
            elif k == "w:before":  # Отступ после параграфа
                css += f"margin-top: {float(v) / 240}em;\n"
        return css

    def from_json(self, json: dict):
        for k, v in json.items():
            if k == "marginTop":
                self.attrib['w:before'] = f"{int(float(v.split('em')[0]) * 240)}"
                self.enabled = True
            elif k == "marginBottom":
                self.attrib['w:after'] = f"{int(float(v.split('em')[0]) * 240)}"
                self.enabled = True
            elif k == "lineHeight":
                self.attrib['w:line'] = f"{int(float(v) * 240)}"
                self.attrib['w:lineRule'] = "auto"
                self.enabled = True


class Indent(Property):

    def __init__(self, elem=None):
        super().__init__()
        self.tag = "w:ind"
        if elem is not None:
            self.attrib = get_attributes(elem.attrib)
            self.enabled = True

    def to_css(self) -> str:
        css = """"""
        for k, v in self.attrib.items():
            if k == "w:left":
                css += f"margin-left: {float(v) / 567}cm;\n"
            elif k == "w:right":  # Отступ до параграфа
                css += f"margin-right: {float(v) / 567}cm;\n"
            elif k == "w:firstLine":  # Отступ после параграфа
                css += f"text-indent: {float(v) / 567}cm;\n"
        return css

    def from_json(self, json: dict):
        for k, v in json.items():
            if k == "marginLeft":
                self.attrib["w:left"] = f"{int(float(v.split('cm')[0]) * 567)}"
                self.enabled = True
            elif k == "marginRight":
                self.attrib['w:right'] = f"{int(float(v.split('cm')[0]) * 567)}"
                self.enabled = True
            elif k == "textIndent":
                self.attrib['w:firstLine'] = f"{int(float(v.split('cm')[0]) * 567)}"
                self.enabled = True


class FontFamily(Property):

    def __init__(self, elem=None):
        super().__init__()
        self.tag = "w:rFonts"
        if elem is not None:
            self.attrib = get_attributes(elem.attrib)
            self.enabled = True

    def to_css(self) -> str:
        print(self.attrib)
        if('w:ascii' in self.attrib):
            return f"""font-family:{self.attrib['w:ascii']};"""
        else:
            return ""

    def from_json(self, json: dict):
        for k, v in json.items():
            if k == "fontFamily":
                self.attrib['w:ascii'] = v[1:-1]
                self.attrib['w:hAnsi'] = v[1:-1]
                self.attrib['w:cs'] = v[1:-1]
                self.enabled = True


class TextColor(Property):

    def __init__(self, elem=None):
        super().__init__()
        self.tag = "w:color"
        if elem is not None:
            self.attrib = get_attributes(elem.attrib)
            self.enabled = True

    def to_css(self) -> str:
        css = """"""
        for k, v in self.attrib.items():
            if k == "w:val":
                css += f"color: #{v};\n"
        return css

    def from_json(self, json: dict):
        for k, v in json.items():
            if k == "color":
                self.attrib["w:val"] = f"{rgb_to_hex(str_to_array(v))}"
                self.enabled = True


class Underline(Property):

    def __init__(self, elem=None):
        super().__init__()
        self.tag = "w:u"
        if elem is not None:
            self.attrib = get_attributes(elem.attrib)
            self.enabled = True

    def to_css(self) -> str:
        css = """"""
        for k, v in self.attrib.items():
            if k == "w:val":
                css += "text-decoration: underline;"
        return css

    def from_json(self, json: dict):
        for k, v in json.items():
            if k == "textDecoration" and v == 'underline':
                self.attrib["w:val"] = "single"
                self.enabled = True


class Bold(Property):

    def __init__(self, elem=None):
        super().__init__()
        self.tag = "w:b"
        if elem is not None:
            self.attrib = get_attributes(elem.attrib)
            self.enabled = True

    def to_css(self) -> str:
        return "font-weight: bold;"

    def from_json(self, json: dict):
        for k, v in json.items():
            if k == "fontWeight":
                self.enabled = True


class Italic(Property):

    def __init__(self, elem=None):
        super().__init__()
        self.tag = "w:i"
        if elem is not None:
            self.attrib = get_attributes(elem.attrib)
            self.enabled = True

    def to_css(self) -> str:
        return "font-style: italic;"

    def from_json(self, json: dict):
        for k, v in json.items():
            if k == "fontStyle":
                self.enabled = True


class FontSize(Property):

    def __init__(self, elem=None):
        super().__init__()
        self.tag = "w:sz"
        if elem is not None:
            self.attrib = get_attributes(elem.attrib)
            self.enabled = True

    def to_css(self) -> str:
        css = """"""
        for k, v in self.attrib.items():
            if k == "w:val":
                css += f"font-size: {int((float(v) / 2) + 3)}px;\n"
        return css

    def from_json(self, json: dict):
        for k, v in json.items():
            if k == "fontSize":
                self.attrib['w:val'] = f"{int((float(v.split('px')[0]) - 3) * 2)}"
                self.enabled = True


class CellWidth(Property):

    def __init__(self, elem=None):
        super().__init__()
        self.tag = "w:tcW"
        if elem is not None:
            self.attrib = get_attributes(elem.attrib)
            self.enabled = True

    def to_css(self) -> str:
        css = """"""
        for k, v in self.attrib.items():
            if k == "w:w":
                css += f"width: {(int(v) / 20) * 1.338307}px;"
        return css

    def from_json(self, json: dict):
        for k, v in json.items():
            if k == "width":
                if "%" in v:
                    return
                self.attrib["w:w"] = f"{int((float(v.split('px')[0]) * 20) / 1.338307)}"
                self.attrib["w:type"] = "dxa"
                self.enabled = True


class HeaderReference(Property):

    def __init__(self, elem=None):
        super().__init__()
        self.tag = "w:headerReference"
        if elem is not None:
            self.attrib = get_attributes(elem.attrib)
            self.enabled = True

    def to_css(self) -> str:
        css = """"""
        for k, v in self.attrib.items():
            if k == "w:type":
                css += f"head_type:{v};"
            elif k == "r:id":
                css += f"head_id:{v}|"
        return css

    def from_json(self, json: dict):
        for k, v in json.items():
            if str(k).startswith("head_type"):
                _type, _id = k.split(";")
                self.attrib["w:type"] = _type.split(":")[1]
                self.attrib["r:id"] = _id.split(":")[1][:-1]
                self.enabled = True


class FooterReference(Property):

    def __init__(self, elem=None):
        super().__init__()
        self.tag = "w:footerReference"
        if elem is not None:
            self.attrib = get_attributes(elem.attrib)
            self.enabled = True

    def to_css(self) -> str:
        css = """"""
        for k, v in self.attrib.items():
            if k == "w:type":
                css += f"foot_type:{v};"
            elif k == "r:id":
                css += f"foot_id:{v}|"
        return css

    def from_json(self, json: dict):
        for k, v in json.items():
            if str(k).startswith("foot_type"):
                _type, _id = k.split(";")
                self.attrib["w:type"] = _type.split(":")[1]
                self.attrib["r:id"] = _id.split(":")[1][:-1]
                self.enabled = True


class PageSize(Property):

    def __init__(self, elem=None):
        super().__init__()
        self.tag = "w:pgSz"
        if elem is not None:
            self.attrib = get_attributes(elem.attrib)
            self.enabled = True

    def to_css(self) -> str:
        css = """"""
        for k, v in self.attrib.items():
            if k == "w:w":
                css += f"sz_w:{v};"
            elif k == "w:h":
                css += f"sz_h:{v}|"
        return css

    def from_json(self, json: dict):
        for k, v in json.items():
            if str(k).startswith("sz_w"):
                _w, _h = k.split(";")
                self.attrib["w:w"] = _w.split(":")[1]
                self.attrib["w:h"] = _h.split(":")[1][:-1]
                self.enabled = True


class PageMargin(Property):

    def __init__(self, elem=None):
        super().__init__()
        self.tag = "w:pgMar"
        if elem is not None:
            self.attrib = get_attributes(elem.attrib)
            self.enabled = True

    def to_css(self) -> str:
        css = """"""
        for k, v in self.attrib.items():
            if k == "w:top":
                css += f"mar_top:{v};"
            elif k == "w:right":
                css += f"mar_right:{v};"
            elif k == "w:bottom":
                css += f"mar_bottom:{v};"
            elif k == "w:left":
                css += f"mar_left:{v};"
            elif k == "w:header":
                css += f"mar_header:{v};"
            elif k == "w:footer":
                css += f"mar_footer:{v};"
            elif k == "w:gutter":
                css += f"mar_gutter:{v}|"
        return css

    def from_json(self, json: dict):
        for k, v in json.items():
            if str(k).startswith("mar_top"):
                _top, _right, _bottom, _left, _header, _footer, _gutter = v.split(";")
                self.attrib["w:top"] = _top.split(":")[1]
                self.attrib["w:right"] = _right.split(":")[1]
                self.attrib["w:bottom"] = _bottom.split(":")[1]
                self.attrib["w:left"] = _left.split(":")[1]
                self.attrib["w:header"] = _header.split(":")[1]
                self.attrib["w:footer"] = _footer.split(":")[1]
                self.attrib["w:gutter"] = _gutter.split(":")[1][:-1]
                self.enabled = True


class Cols(Property):

    def __init__(self, elem=None):
        super().__init__()
        self.tag = "w:cols"
        if elem is not None:
            self.attrib = get_attributes(elem.attrib)
            self.enabled = True

    def to_css(self) -> str:
        css = """"""
        for k, v in self.attrib.items():
            if k == "w:space":
                css += f"cols_space:{v}|"
        return css

    def from_json(self, json: dict):
        for k, v in json.items():
            if str(k).startswith("cols_space"):
                self.attrib["w:space"] = v.split(":")[1][:-1]
                self.enabled = True


class DocGrid(Property):

    def __init__(self, elem=None):
        super().__init__()
        self.tag = "w:docGrid"
        if elem is not None:
            self.attrib = get_attributes(elem.attrib)
            self.enabled = True

    def to_css(self) -> str:
        css = """"""
        for k, v in self.attrib.items():
            if k == "w:linePitch":
                css += f"linePitch:{v}|"
        return css

    def from_json(self, json: dict):
        for k, v in json.items():
            if str(k).startswith("linePitch"):
                self.attrib["w:linePitch"] = v.split(":")[1][:-1]
                self.enabled = True


class Borders(Property):

    def __init__(self, elem=None):
        super().__init__()
        self.tag = "w:tcBorders"
        self.borders = {}
        if elem is not None:
            self.attrib = get_attributes(elem.attrib)
            self.enabled = True
            for border in elem:
                self.borders[f"w:{border.tag.split('}')[1]}"] = get_attributes(border.attrib)

    def to_lxml(self):
        elem = create_element(self.tag)
        for k, v in self.attrib.items():
            create_attribute(elem, k, v)
        for k, v in self.borders.items():
            border = create_element(f"w:{k}")
            for attr_name, attr_value in v.items():
                create_attribute(border, attr_name, attr_value)
            elem.append(border)
        return elem

    def to_css(self) -> str:
        css = """"""
        for k, v in self.borders.items():
            val = v["w:val"]
            if val == "nil":
                css += f"border-{str(k).split(':')[1]}: 0px;"
            else:
                sz = v['w:sz']  # Толщина
                space = v['w:space']  # Не разобрался, не используют пока что
                color = v['w:color']  # Цвет
                css += f"border-{str(k).split(':')[1]}: {int(int(sz) / 4)}px {'solid' if val == 'single' else 'solid'} {'black' if color == 'auto' else color};"
        return css

    def from_json(self, json: dict):
        for k, v in json.items():
            if k == "borderBottom":
                splitted_json = v.split(" ")
                if splitted_json[0] == "0px":
                    self.borders["w:bottom"] = {"w:val": "nil"}
                else:
                    self.borders[f"bottom"] = {"w:val": "single", "w:sz": str(int(splitted_json[0].split("px")[0]) * 4),
                                               "w:space": "0", "w:color": "auto"}
                self.enabled = True
            elif k == "borderTop":
                splitted_json = v.split(" ")
                if splitted_json[0] == "0px":
                    self.borders["w:top"] = {"w:val": "nil"}
                else:
                    self.borders[f"top"] = {"w:val": "single", "w:sz": str(int(splitted_json[0].split("px")[0]) * 4),
                                            "w:space": "0", "w:color": "auto"}
                self.enabled = True
            elif k == "borderLeft":
                splitted_json = v.split(" ")
                if splitted_json[0] == "0px":
                    self.borders["w:left"] = {"w:val": "nil"}
                else:
                    self.borders[f"left"] = {"w:val": "single", "w:sz": str(int(splitted_json[0].split("px")[0]) * 4),
                                             "w:space": "0", "w:color": "auto"}
                self.enabled = True
            elif k == "borderRight":
                splitted_json = v.split(" ")
                if splitted_json[0] == "0px":
                    self.borders["w:right"] = {"w:val": "nil"}
                else:
                    self.borders[f"right"] = {"w:val": "single", "w:sz": str(int(splitted_json[0].split("px")[0]) * 4),
                                              "w:space": "0", "w:color": "auto"}
                self.enabled = True


class PropertiesCollection:

    def __init__(self, element=None, tag=""):
        self.properties = {}
        self.tag = tag
        self.properties = {"Align": Align(None),
                        #    "Background": Background(None),
                           "Highlight": Highlight(None),
                           "Spacing": Spacing(None),
                           "Indent": Indent(None),
                           "FontFamily": FontFamily(None),
                           "FontSize": FontSize(None),
                           "TextColor": TextColor(None),
                           "Underline": Underline(None),
                           "Bold": Bold(None),
                           "Italic": Italic(None),
                           "CellWidth": CellWidth(None),
                           "HeaderReference": HeaderReference(None),
                           "FooterReference": FooterReference(None),
                           "PageSize": PageSize(None),
                           "PageMargin": PageMargin(None),
                           "Cols": Cols(None),
                           "DocGrid": DocGrid(None),
                           "Borders": Borders(None)}
        if element is not None:
            for prop in element:
                if prop.tag == f"{vst}jc":
                    self.properties["Align"] = Align(prop)
                elif prop.tag == f"{vst}shd":
                    self.properties["Background"] = Background(prop)
                elif prop.tag == f"{vst}spacing":
                    self.properties["Spacing"] = Spacing(prop)
                elif prop.tag == f"{vst}ind":
                    self.properties["Indent"] = Indent(prop)
                elif prop.tag == f"{vst}rFonts":
                    self.properties["FontFamily"] = FontFamily(prop)
                elif prop.tag == f"{vst}sz":
                    self.properties["FontSize"] = FontSize(prop)
                elif prop.tag == f"{vst}color":
                    self.properties["TextColor"] = TextColor(prop)
                elif prop.tag == f"{vst}u":
                    self.properties["Underline"] = Underline(prop)
                elif prop.tag == f"{vst}b":
                    self.properties["Bold"] = Bold(prop)
                elif prop.tag == f"{vst}i":
                    self.properties["Italic"] = Italic(prop)
                elif prop.tag == f"{vst}tcW":
                    self.properties["CellWidth"] = CellWidth(prop)
                elif prop.tag == f"{vst}tcBorders":
                    self.properties["Borders"] = Borders(prop)
                elif prop.tag == f"{vst}headerReference":
                    self.properties["HeaderReference"] = HeaderReference(prop)
                elif prop.tag == f"{vst}footerReference":
                    self.properties["FooterReference"] = FooterReference(prop)
                elif prop.tag == f"{vst}pgSz":
                    self.properties["PageSize"] = PageSize(prop)
                elif prop.tag == f"{vst}pgMar":
                    self.properties["PageMargin"] = PageMargin(prop)
                elif prop.tag == f"{vst}cols":
                    self.properties["Cols"] = Cols(prop)
                elif prop.tag == f"{vst}docGrid":
                    self.properties["DocGrid"] = DocGrid(prop)
                elif prop.tag == f"{vst}tcBorders":
                    self.properties["Borders"] = Borders(prop)
                # else:
                #     print("%%%% ", prop.tag, " %%%%")

    def to_css(self):
        css = """"""
        for k, v in self.properties.items():
            if v.enabled:
                css += v.to_css()
        return css

    def from_json(self, json):
        for k, v in self.properties.items():
            v.from_json(json)

    def to_lxml(self):
        return [v.to_lxml() for k, v in self.properties.items() if v.enabled]
