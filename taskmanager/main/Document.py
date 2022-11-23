# Класс Документ, представленный xml структурой

from .WordAPI import WordAPI, create_element, create_attribute
from abc import ABC, abstractmethod
import re

# Специальные xml вставки от Microsoft, нужны, чтобы сравнивать теги
vst = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
vst1 = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
vst2 = "{http://schemas.openxmlformats.org/markup-compatibility/2006}"

vsts = {"{http://schemas.openxmlformats.org/wordprocessingml/2006/main}": "w", "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}": "r"}


# Абстрактный класс Элемент
class Element(ABC):

    # Метод, который выводит текст, содержащийся в элементе
    @abstractmethod
    def text(self):
        pass

    # Метод, который переводит элемент в lxml word для формирования документа Microsoft Word
    @abstractmethod
    def to_lxml(self):
        pass


# Объект, который просто копирует xml структуру элемента, для которого не описан класс
class Else(Element):

    def __init__(self, elem):
        self.tag = elem.tag.split('}')[1]
        self.elem = elem
        self.childs = {}

    def text(self):
        return ""

    def to_lxml(self):
        return self.elem


# Класс-паттерн фабрика для создания объектов класса Element
class ElementFactory:

    def __init__(self):
        pass

    def initialize(self, elem):
        tag = elem.tag.split('}')[1]
        if tag.find("Pr") != -1:
            return Properties(elem)
        elif tag == "r":
            return Run(elem)
        elif tag == "hyperlink":
            return Hyperlink(elem)
        elif tag == "p":
            return Paragraph(elem)
        elif tag == "t":
            return Text(elem)
        else:
            return Else(elem)

    # Функция возвращает потомков Элемента elem.
    # Пока что эта функция в классе фабрика, что не правильно, но не знаю куда лучше разместить
    def get_childs(self, elem):
        childs = {}
        i = 0
        for son in elem:
            childs[son.tag.split('}')[1]+f"_{i}"] = self.initialize(son)
            i += 1
        return childs


# Элемент свойства
class Properties(Element):

    def __init__(self, elem):
        self.tag = elem.tag.split('}')[1]
        self.pr = {}
        self.childs = {}
        self.attrib = {f"{vsts[i.split('}')[0]+'}']}:" + i.split('}')[1]: elem.attrib[i] for i in elem.attrib}
        for prop in elem:
            if prop.tag == f"{vst}rPr" or prop.tag == f"{vst}sectPr":
                self.pr[prop.tag.split('}')[1]+f"_:{len(self.pr)}"] = Properties(prop)
            else:
                self.pr[prop.tag.split('}')[1]+f"_:{len(self.pr)}"] = {f"{vsts[i.split('}')[0]+'}']}:" + i.split('}')[1]: prop.attrib[i] for i in list(prop.keys())}

    def to_lxml(self):
        Pr = create_element(f"w:{self.tag}")
        for k in list(self.attrib.keys()):
            create_attribute(Pr, f"{k}", self.attrib[k])
        for child in self.pr:
            child_tag = child.split('_:')[0]
            if child_tag == "rPr" or child_tag == "sectPr":
                ch = self.pr[child].to_lxml()
            else:
                ch = create_element(f"w:{child_tag}")
                for attr in self.pr[child]:
                    create_attribute(ch, f"{attr}", self.pr[child][attr])
            Pr.append(ch)
        return Pr

    def text(self):
        return ""

    def to_css(self):
        pass

    def __eq__(self, other):
        if self.pr == other.pr:
            return True
        else:
            return False


# Класс Ссылка
class Hyperlink(Element):
    def __init__(self, elem):
        self.tag = 'hyperlink'
        self.Id = elem.attrib[f'{vst1}id']
        self.history = elem.attrib[f"{vst}history"]
        self.attrib = {f"{vsts[i.split('}')[0]+'}']}:" + i.split('}')[1]: elem.attrib[i] for i in elem.attrib}
        self.childs = ElementFactory().get_childs(elem)

    def text(self):
        text = ""
        for child in list(self.childs.values()):
            text += child.text()
        return text

    def to_lxml(self):
        hyperlink = create_element("w:hyperlink")
        for k in list(self.attrib.keys()):
            create_attribute(hyperlink, f"{k}", self.attrib[k])
        for child in list(self.childs.values()):
            try:
                hyperlink.append(child.to_lxml())
            except Exception as exc:
                print(exc, "Hyperlink to lxml")
        return hyperlink


# Класс Текстовый элемент
class Text(Element):
    def __init__(self, elem):
        self.tag = 't'
        self.textElem = elem.text
        self.attrib = {i.split('}')[1]: elem.attrib[i] for i in elem.attrib}

    def text(self):
        return self.textElem

    def to_lxml(self):
        try:
            t = create_element("w:t")
            for k in list(self.attrib.keys()):
                create_attribute(t, f"xml:{k}", self.attrib[k])
            t.text = self.textElem
            return t
        except Exception as exc:
            print(exc, "Text to lxml", self.textElem)


# Класс пробег по параграфу
class Run(Element):

    def __init__(self, elem):
        self.tag = 'r'
        self.attrib = {f"{vsts[i.split('}')[0]+'}']}:" + i.split('}')[1]: elem.attrib[i] for i in elem.attrib}
        self.childs = ElementFactory().get_childs(elem)

    def text(self):
        t = ""
        for son in list(self.childs.keys()):
            if re.fullmatch("t_\d*", son):
                t += self.childs[son].text()
        return t

    def to_lxml(self):
        r = create_element("w:r")
        for k in list(self.attrib.keys()):
            create_attribute(r, f"{k}", self.attrib[k])
        for child in list(self.childs.values()):
            try:
                r.append(child.to_lxml())
            except Exception as exc:
                print(exc, "Run to lxml", child)
        return r


# Класс параграф
class Paragraph(Element):

    def __init__(self, elem):
        self.tag = 'p'
        self.pr = Properties(elem[0])
        self.attrib = {f"{vsts[i.split('}')[0]+'}']}:" + i.split('}')[1]: elem.attrib[i] for i in elem.attrib}
        self.childs = ElementFactory().get_childs(elem)

    def text(self):
        text = ""
        for child in list(self.childs.values()):
            text += child.text()
        return text

    def to_lxml(self):
        p = create_element("w:p")
        for k in list(self.attrib.keys()):
            create_attribute(p, f"{k}", self.attrib[k])
        for child in list(self.childs.values()):
            try:
                p.append(child.to_lxml())
            except Exception as exc:
                print(exc, "Paragraph to lxml", child)
        return p


# Класс документ
class Document:

    def __init__(self, path):
        self.wa = WordAPI(path)
        body = self.wa.get_elements_by_tag("w:body")[0]

        self.childs = ElementFactory().get_childs(body)

    def save(self):
        self.wa.create_new_doc(list(self.childs.values()))
        self.wa.saveDoc("121.docx")

