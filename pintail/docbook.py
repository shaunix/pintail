# pintail - Build static sites from collections of Mallard documents
# Copyright (c) 2015 Shaun McCance <shaunm@gnome.org>
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 2 of the License, or
# (at your option) any later version.

# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.

# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

import os
import subprocess
from lxml import etree

import pintail.site

XML_NS = '{http://www.w3.org/XML/1998/namespace}'
XLINK_NS = '{https://www.w3.org/1999/xlink}'
MAL_NS = '{http://projectmallard.org/1.0/}'
SITE_NS = '{http://projectmallard.org/site/1.0/}'
PINTAIL_NS = '{http://pintail.io/}'
DOCBOOK_NS = '{http://docbook.org/ns/docbook}'
DOCBOOK_CHUNKS_ = [
    'appendix', 'article', 'bibliography', 'bibliodiv', 'book', 'chapter', 'colophon',
    'dedication', 'glossary', 'glossdiv', 'index', 'lot', 'part', 'preface', 'refentry',
    'reference', 'sect1', 'sect2', 'sect3', 'sect4', 'sect5', 'section', 'setindex',
    'simplesect', 'toc']
DOCBOOK_CHUNKS = DOCBOOK_CHUNKS_ + [DOCBOOK_NS + el for el in DOCBOOK_CHUNKS_]
DOCBOOK_INFOS = [
    DOCBOOK_NS + 'info', 'appendixinfo', 'articleinfo', 'bibliographyinfo', 'bookinfo',
    'chapterinfo', 'glossaryinfo', 'indexinfo', 'partinfo', 'prefaceinfo', 'refentryinfo',
    'referenceinfo', 'sect1info', 'sect2info', 'sect3info', 'sect4info', 'sect5info',
    'sectioninfo', 'setindexinfo']

class DocBookPage(pintail.site.Page, pintail.site.ToolsProvider, pintail.site.CssProvider):

    _html_transform = None

    def __init__(self, directory, source_file):
        pintail.site.Page.__init__(self, directory, source_file)
        self.stage_page()
        self._tree = etree.parse(self.get_stage_path())
        maxdepth = 1
        if self._tree.getroot().tag in ('book', DOCBOOK_NS + 'book'):
            maxdepth = 2
        pi = self._tree.getroot().xpath('string(/processing-instruction("db.chunk.max_depth"))')
        if len(pi) > 0:
            try:
                maxdepth = int(pi)
            except:
                pass
        self.maxdepth = maxdepth

        self._fixed = False
        self._fixid = 1
        def _fixids(node):
            if node.tag in DOCBOOK_CHUNKS:
                chunkid = node.get('id') or node.get(XML_NS + 'id')
                if chunkid is None:
                    if node is self._tree.getroot():
                        chunkid = 'index'
                    else:
                        while self._tree.xpath('count(//*[@id = "%s" or @xml:id = "%s"])' %
                                               ('page' + str(self._fixid), 'page' + str(self._fixid))) > 0:
                            self._fixid += 1
                        chunkid = 'page' + str(self._fixid)
                    if node.tag.startswith(DOCBOOK_NS):
                        node.set(XML_NS + 'id', chunkid)
                    else:
                        node.set('id', chunkid)
                    self._fixed = True
                for child in node:
                    _fixids(child)
        _fixids(self._tree.getroot())
        if self._fixed:
            self._tree.write(self.get_stage_path())

        def _accumulate_pages(node, depth, maxdepth):
            ret = []
            for child in node:
                if child.tag in DOCBOOK_CHUNKS:
                    ret.append(child)
                    if depth < maxdepth:
                        ret.extend(_accumulate_pages(child, depth + 1, maxdepth))
            return ret
        pages = _accumulate_pages(self._tree.getroot(), 1, maxdepth)
        self.subpages = [DocBookSubPage(self, el) for el in pages]
        self._langtrees = {}
        self._notlangs = set()

    def _get_tree(self, lang=None):
        if lang is None or lang in self._notlangs:
            return self._tree
        if lang in self._langtrees:
            return self._langtrees[lang]
        if self.directory.translation_provider.translate_page(self, lang):
            self._langtrees[lang] = etree.parse(self.get_stage_path(lang))
            return self._langtrees[lang]
        else:
            self._notlangs.add(lang)
            return self._tree

    @property
    def page_id(self):
        return 'index'

    @property
    def searchable(self):
        return True

    def get_title_node(self, node, hint=None):
        title = ''
        for child in node:
            if child.tag in DOCBOOK_INFOS:
                for info in child:
                    if info.tag in ('title', DOCBOOK_NS + 'title'):
                        title = info.xpath('string(.)')
            elif child.tag in ('title', DOCBOOK_NS + 'title'):
                title = child.xpath('string(.)')
                break
        return title

    def get_title(self, hint=None, lang=None):
        return self.get_title_node(self._get_tree(lang).getroot(), hint=hint)

    def get_content_node(self, node, hint=None):
        depth = 0
        parent = node.getparent()
        while parent is not None:
            depth += 1
            parent = parent.getparent()
        def _accumulate_text(node):
            ret = ''
            for child in node:
                if not isinstance(child.tag, str):
                    continue
                if node.tag in DOCBOOK_INFOS:
                    continue
                if depth < self.maxdepth and child.tag in DOCBOOK_CHUNKS:
                    continue
                ret += child.text or ''
                ret += _accumulate_text(child)
                ret += child.tail or ''
            return ret
        return _accumulate_text(node)

    def get_content(self, hint=None, lang=None):
        return self.get_content_node(self._get_tree(lang).getroot(), hint=hint)

    @classmethod
    def build_tools(cls, site):
        db2html = os.path.join(site.yelp_xsl_path, 'xslt', 'docbook', 'html', 'db2html.xsl')
        mallink = os.path.join(site.yelp_xsl_path, 'xslt', 'mallard', 'common', 'mal-link.xsl')

        fd = open(os.path.join(site.tools_path, 'pintail-html-docbook-local.xsl'), 'w')
        fd.write('<xsl:stylesheet' +
                 ' xmlns:xsl="http://www.w3.org/1999/XSL/Transform"' +
                 ' version="1.0">\n' +
                 '<xsl:import href="pintail-html-docbook.xsl"/>\n' +
                 '<xsl:param name="db.chunk.extension" select="$pintail.extension.link"/>\n')
        for xsl in site.get_custom_xsl():
            fd.write('<xsl:include href="%s"/>\n' % xsl)
        fd.write('</xsl:stylesheet>')
        fd.close()

        fd = open(os.path.join(site.tools_path, 'pintail-html-docbook.xsl'), 'w')
        fd.write(('<xsl:stylesheet' +
                  ' xmlns:xsl="http://www.w3.org/1999/XSL/Transform"' +
                  ' version="1.0">\n' +
                  '<xsl:import href="%s"/>\n' +
                  '<xsl:import href="%s"/>\n' +
                  '<xsl:include href="%s"/>\n' +
                  '</xsl:stylesheet>\n')
                 % (db2html, mallink, 'pintail-html.xsl'))
        fd.close()

    @classmethod
    def build_css(cls, site):
        xslpath = os.path.join(site.yelp_xsl_path, 'xslt')

        pintail.site.Site._makedirs(site.tools_path)
        cssxsl = os.path.join(site.tools_path, 'pintail-css-docbook.xsl')
        fd = open(cssxsl, 'w')
        fd.writelines([
            '<xsl:stylesheet',
            ' xmlns:xsl="http://www.w3.org/1999/XSL/Transform"',
            ' xmlns:exsl="http://exslt.org/common"',
            ' extension-element-prefixes="exsl"',
            ' version="1.0">\n',
            '<xsl:import href="' + xslpath + '/common/l10n.xsl"/>\n',
            '<xsl:import href="' + xslpath + '/common/color.xsl"/>\n',
            '<xsl:import href="' + xslpath + '/common/icons.xsl"/>\n',
            '<xsl:import href="' + xslpath + '/common/html.xsl"/>\n',
            '<xsl:import href="' + xslpath + '/docbook/html/db2html-css.xsl"/>\n'
            ])
        for xsl in site.get_custom_xsl():
            fd.write('<xsl:include href="%s"/>\n' % xsl)
        fd.writelines([
            '<xsl:output method="text"/>\n',
            '<xsl:param name="out"/>\n',
            '<xsl:template match="/">\n',
            '<xsl:for-each select="/*">\n',
            '<xsl:variable name="locale">\n',
            ' <xsl:choose>\n',
            '  <xsl:when test="@xml:lang">\n',
            '   <xsl:value-of select="@xml:lang"/>\n',
            '  </xsl:when>\n',
            '  <xsl:when test="@lang">\n',
            '   <xsl:value-of select="@lang"/>\n',
            '  </xsl:when>\n',
            '  <xsl:otherwise>\n',
            '   <xsl:text>C</xsl:text>\n',
            '  </xsl:otherwise>\n',
            ' </xsl:choose>\n',
            '</xsl:variable>\n',
            '<exsl:document href="{$out}" method="text">\n',
            ' <xsl:call-template name="html.css.content"/>\n',
            '</exsl:document>\n',
            '</xsl:for-each>\n',
            '</xsl:template>\n'
            '</xsl:stylesheet>\n'
            ])
        fd.close()

        seenlangs = []
        for page in site.root.iter_pages():
            if not isinstance(page, DocBookPage):
                continue
            for lc in [None] + site.get_langs():
                try:
                    doc = page._get_tree(lc).getroot()
                    lang = doc.get(XML_NS + 'lang', doc.get('lang', 'C'))
                except:
                    continue
                if lang in seenlangs:
                    continue
                seenlangs.append(lang)
                cssfile = 'pintail-docbook-' + lang + '.css'
                csspath = os.path.join(site.target_path, cssfile)
                site.log('CSS', '/' + cssfile)
                subprocess.call(['xsltproc',
                                 '-o', site.target_path,
                                 '--stringparam', 'out', csspath,
                                 cssxsl, page.get_stage_path(lc)])
                custom_css = site.config.get('custom_css')
                if custom_css is not None:
                    custom_css = os.path.join(site.topdir, custom_css)
                    fd = open(csspath, 'a')
                    fd.write(open(custom_css).read())
                    fd.close()

    def stage_page(self):
        pintail.site.Site._makedirs(self.directory.get_stage_path())
        subprocess.call(['xmllint', '--xinclude', '--noent',
                         '-o', self.get_stage_path(),
                         self.get_source_path()])

    def get_cache_data(self, lang=None):
        ret = None
        try:
            ret = etree.Element(PINTAIL_NS + 'external')
            ret.set('id', self.directory.path + 'index')
            ret.set(SITE_NS + 'dir', self.directory.path)
            dbfile = self._get_tree(lang)
            dbfile.xinclude()
            info = None
            title = None
            for child in dbfile.getroot():
                if not isinstance(child.tag, str):
                    continue
                if child.tag == (DOCBOOK_NS + 'info'):
                    info = child
                elif etree.QName(child.tag).namespace is None and child.tag.endswith('info'):
                    info = child
                elif child.tag in ('title', DOCBOOK_NS + 'title'):
                    title = child
                    break
            if title is None and info is not None:
                for child in info:
                    if child.tag in ('title', DOCBOOK_NS + 'title'):
                        title = child
                        break
            if title is not None:
                title = title.xpath('string(.)')
                titlen = etree.Element(MAL_NS + 'title')
                titlen.text = title
                ret.append(titlen)
        except:
            pass
        return ret

    def build_html(self, lang=None):
        if lang is None:
            self.site.log('HTML', self.site_id)
        else:
            self.site.log('HTML', lang + ' ' + self.site_id)

        if DocBookPage._html_transform is None:
            DocBookPage._html_transform = etree.XSLT(etree.parse(os.path.join(self.site.tools_path,
                                                                              'pintail-html-docbook-local.xsl')))
        args = {}
        args['pintail.format'] = 'docbook'
        for pair in pintail.site.XslProvider.get_all_xsl_params('html', self, lang=lang):
            args[pair[0]] = etree.XSLT.strparam(pair[1])
        tree = self._get_tree(lang)
        DocBookPage._html_transform(tree, **args)

        return
        # Leaving in this code to call xsltproc for now. It turns out that using
        # etree.XSLT is slower on each individual run than calling xsltproc, oddly
        # enough. But it gets you performance gains over large numbers of documents
        # by not constantly reparsing the XSLT. This is definitely worthwhile for
        # Mallard. We may find it's not worthwhile for DocBook when tested against
        # real-world sites.

        cmd = ['xsltproc',
               '--xinclude',
               '--stringparam', 'pintail.format', 'docbook']
        cmd.extend(pintail.site.XslProvider.get_xsltproc_args('html', self, lang=lang))
        cmd.extend([
            '-o', self.get_target_path(lang),
            os.path.join(self.site.tools_path, 'pintail-html-docbook-local.xsl'),
            self.get_stage_path(lang)])
        subprocess.call(cmd)

    def get_media(self):
        refs = set()
        def _accumulate_refs(node):
            src = node.get('fileref', None)
            if src is not None and ':' not in src:
                refs.add(src)
            href = node.get(XLINK_NS + 'href', None)
            if href is not None and ':' not in href:
                refs.add(href)
            if node.tag == 'ulink':
                href = node.get('url', None)
                if href is not None and ':' not in href:
                    refs.add(href)
            for child in node:
                _accumulate_refs(child)
        _accumulate_refs(self._tree.getroot())
        return refs

    @classmethod
    def get_pages(cls, directory, filename):
        dbfile = directory.site.config.get('docbook', directory.path)
        if filename == dbfile:
            toppage = DocBookPage(directory, filename)
            return [toppage] + toppage.subpages
        return []

class DocBookSubPage(pintail.site.Page):
    def __init__(self, db_page, element):
        pintail.site.Page.__init__(self, db_page.directory, db_page.source_file)
        self._db_page = db_page
        self._sect_id = element.get('id') or element.get(XML_NS + 'id')

    @property
    def page_id(self):
        return self._sect_id

    @property
    def searchable(self):
        return True

    def get_title(self, hint=None, lang=None):
        el = self._db_page._get_tree(lang).getroot().xpath('//*[@id = "%s" or @xml:id = "%s"]' %
                                                           (self._sect_id, self._sect_id))
        return self._db_page.get_title_node(el[0], hint=hint)

    def get_content(self, hint=None, lang=None):
        el = self._db_page._get_tree(lang).getroot().xpath('//*[@id = "%s" or @xml:id = "%s"]' %
                                                           (self._sect_id, self._sect_id))
        return self._db_page.get_content_node(el[0], hint=hint)
