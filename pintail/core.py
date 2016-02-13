# pintail - Build static sites from collections of Mallard documents
# Copyright (c) 2015 Shaun McCance <shaunm@gnome.org>
#
# This program is free software; you can redistribute it and/or modify it under
# the terms of the GNU Lesser General Public License as published by the Free
# Software Foundation; either version 2 of the License, or (at your option) any
# later version.
#
# This program is distributed in the hope that it will be useful, but WITHOUT
# ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS
# FOR A PARTICULAR PURPOSE. See the GNU Lesser General Public License for more
# details.
#
# You should have received a copy of the GNU Lesser General Public License
# along with this program; see the file COPYING.LGPL.  If not, write to the
# Free Software Foundation, Inc., 59 Temple Place - Suite 330, Boston, MA
# 02111-1307, USA.

import codecs
import configparser
import glob
import os
import shutil
import subprocess
import sys

from lxml import etree

MAL_NS = '{http://projectmallard.org/1.0/}'
CACHE_NS = '{http://projectmallard.org/cache/1.0/}'
SITE_NS = '{http://projectmallard.org/site/1.0/}'


class DuplicatePageException(Exception):
    def __init__(self, directory, message):
        self.message = message
        self.parser = directory


class Extendable:
    @classmethod
    def iter_subclasses(cls):
        for cls in cls.__subclasses__():
            yield cls
            yield from cls.iter_subclasses()


class ToolsProvider(Extendable):
    @classmethod
    def build_tools(cls, site):
        pass


class Page(Extendable):
    def __init__(self, directory, source_file):
        self.site = directory.site
        self.directory = directory
        self.source_file = source_file
        self.source_path = os.path.join(directory.source_path, source_file)
        self.target_extension = self.site.config.get('html_extension') or '.html'
        self.page_id = None

    @property
    def stage_file(self):
        return self.source_file

    @property
    def stage_path(self):
        return os.path.join(self.directory.stage_path, self.stage_file)

    @property
    def target_file(self):
        return self.page_id + self.target_extension

    @property
    def target_path(self):
        return os.path.join(self.directory.target_path, self.target_file)

    @property
    def site_id(self):
        return self.directory.path + self.page_id

    def get_cache_data(self):
        return None

    def build_html(self):
        return

    @classmethod
    def get_pages(cls, directory, filename):
        return []


class MallardPage(Page, ToolsProvider):
    def __init__(self, directory, source_file):
        Page.__init__(self, directory, source_file)
        self.stage_page()
        self._tree = etree.parse(self.stage_path)
        etree.XInclude()(self._tree.getroot())
        self.page_id = self._tree.getroot().get('id')
        self.mal2html = os.path.join(self.site.tools_path, 'pintail-html-mallard-local.xsl')

    @classmethod
    def build_tools(cls, site):
        mal2html = os.path.join(site.yelp_xsl_path, 'xslt', 'mallard', 'html', 'mal2html.xsl')

        fd = open(os.path.join(site.tools_path, 'pintail-html-mallard-local.xsl'), 'w')
        fd.write('<xsl:stylesheet' +
                 ' xmlns:xsl="http://www.w3.org/1999/XSL/Transform"' +
                 ' version="1.0">\n' +
                 '<xsl:import href="pintail-html-mallard.xsl"/>\n')
        html_extension = site.config.get('html_extension') or '.html'
        fd.write('<xsl:param name="html.extension" select="' +
                 "'" + html_extension + "'" + '"/>\n')
        link_extension = site.config.get('link_extension')
        if link_extension is not None:
            fd.write('<xsl:param name="mal.link.extension" select="' +
                     "'" + link_extension + "'" + '"/>\n')
        custom_xsl = site.config.get('custom_xsl')
        if custom_xsl is not None:
            custom_xsl = os.path.join(site.topdir, custom_xsl)
            fd.write('<xsl:include href="%s"/>\n' % custom_xsl)
        fd.write('</xsl:stylesheet>')
        fd.close()

        fd = open(os.path.join(site.tools_path, 'pintail-html-mallard.xsl'), 'w')
        fd.write(('<xsl:stylesheet' +
                  ' xmlns:xsl="http://www.w3.org/1999/XSL/Transform"' +
                  ' version="1.0">\n' +
                  '<xsl:import href="%s"/>\n' +
                  '<xsl:include href="%s"/>\n' +
                  '</xsl:stylesheet>\n')
                 % (mal2html, 'pintail-html.xsl'))
        fd.close()

    def stage_page(self):
        Site._makedirs(self.directory.stage_path)
        subprocess.call(['xmllint', '--xinclude',
                         '-o', self.stage_path,
                         self.source_path])

    def get_cache_data(self):
        def _get_node_cache(node):
            ret = etree.Element(node.tag)
            ret.text = '\n'
            ret.tail = '\n'
            for attr in node.keys():
                if attr != 'id':
                    ret.set(attr, node.get(attr))
            if node.tag == MAL_NS + 'page':
                ret.set('id', self.site_id)
            elif node.get('id', None) is not None:
                ret.set('id', self.site_id + '#' + node.get('id'))
            ret.set(SITE_NS + 'dir', self.directory.path)
            for child in node:
                if child.tag == MAL_NS + 'info':
                    info = etree.Element(child.tag)
                    ret.append(info)
                    for infochild in child:
                        if infochild.tag == MAL_NS + 'link':
                            xref = infochild.get('xref', None)
                            if xref is None or xref.startswith('/'):
                                info.append(infochild)
                            else:
                                link = etree.Element(infochild.tag)
                                link.set('xref', self.directory.path + xref)
                                for attr in infochild.keys():
                                    if attr != 'xref':
                                        link.set(attr, infochild.get(attr))
                                for linkchild in infochild:
                                    link.append(linkchild)
                                info.append(link)
                        else:
                            info.append(infochild)
                if child.tag == MAL_NS + 'title':
                    ret.append(child)
                elif child.tag == MAL_NS + 'section':
                    ret.append(_get_node_cache(child))
            return ret
        page = _get_node_cache(self._tree.getroot())
        page.set(CACHE_NS + 'href', self.stage_path)
        return page

    def build_html(self):
        self.site.echo('HTML', self.directory.path, self.target_file)
        subprocess.call(['xsltproc',
                         '--stringparam', 'mal.cache.file', self.site.cache_path,
                         '--stringparam', 'pintail.site.dir', self.directory.path,
                         '--stringparam', 'pintail.site.root',
                         self.site.config.get('site_root') or '/',
                         '-o', self.target_path,
                         self.mal2html, self.stage_path])

    def get_media(self):
        refs = set()
        def _accumulate_refs(node):
            src = node.get('src', None)
            if src is not None and ':' not in src:
                refs.add(src)
            href = node.get('href', None)
            if href is not None and ':' not in href:
                refs.add(href)
            for child in node:
                _accumulate_refs(child)
        _accumulate_refs(self._tree.getroot())
        return refs

    @classmethod
    def get_pages(cls, directory, filename):
        if filename.endswith('.page'):
            return [MallardPage(directory, filename)]
        return []


class DucktypePage(MallardPage):
    def __init__(self, directory, source_file):
        MallardPage.__init__(self, directory, source_file)

    def stage_page(self):
        Site._makedirs(self.directory.stage_path)
        subprocess.call(['ducktype',
                         '-o', self.stage_path,
                         self.source_path])

    @property
    def stage_file(self):
        if self.source_file.endswith('.duck'):
            return self.source_file[:-5] + '.page'
        else:
            return self.source_file

    @classmethod
    def get_pages(cls, directory, filename):
        if filename.endswith('.duck'):
            return [DucktypePage(directory, filename)]
        return []


class Directory(Extendable):
    def __init__(self, site, path, *, parent=None):
        if not hasattr(self, 'site'):
            self.site = site
        if not hasattr(self, 'path'):
            self.path = path
        if not hasattr(self, 'parent'):
            self.parent = parent
        if not hasattr(self, 'directories'):
            self.directories = []
        if not hasattr(self, 'pages'):
            self.pages = []

        self.read_directories()
        self.read_pages()

    @classmethod
    def is_special_path(cls, site, path):
        return False

    @property
    def source_path(self):
        if self.parent is not None:
            return os.path.join(self.parent.source_path,
                                self.path.split('/')[-2])
        else:
            return os.path.join(self.site.topdir, self.path[1:])

    @property
    def stage_path(self):
        return os.path.join(self.site.stage_path, self.path[1:])

    @property
    def target_path(self):
        return os.path.join(self.site.target_path, self.path[1:])

    def read_directories(self):
        for name in os.listdir(self.source_path):
            if os.path.isdir(os.path.join(self.source_path, name)):
                subpath = self.path + name + '/'
                if self.site.get_ignore_directory(subpath):
                    continue
                subdir = None
                for cls in Directory.iter_subclasses():
                    if cls.is_special_path(self.site, subpath):
                        subdir = cls(self.site, subpath, parent=self)
                        break
                if subdir is None:
                    subdir = Directory(self.site, subpath, parent=self)
                self.directories.append(subdir)

    def read_pages(self):
        by_page_id = {}
        for name in os.listdir(self.source_path):
            if os.path.isfile(os.path.join(self.source_path, name)):
                for cls in Page.iter_subclasses():
                    for page in cls.get_pages(self, name):
                        if page.page_id in by_page_id:
                            raise DuplicatePageException(self,
                                                         'Duplicate page id ' +
                                                         page.page_id)
                        by_page_id[page.page_id] = page
                        self.pages.append(page)

    def iter_directories(self):
        yield self
        for subdir in self.directories:
            yield from subdir.iter_directories()

    def iter_pages(self):
        for page in self.pages:
            yield page
        for subdir in self.directories:
            yield from subdir.iter_pages()

    def build_html(self):
        Site._makedirs(self.target_path)
        for subdir in self.directories:
            subdir.build_html()
        for page in self.pages:
            page.build_html()

    def build_media(self):
        Site._makedirs(self.target_path)
        for subdir in self.directories:
            subdir.build_media()
        media = set()
        for page in self.pages:
            media.update(page.get_media())
        for fname in media:
            if fname.startswith('/'):
                source = os.path.join(self.site.topdir, fname[1:])
                target = os.path.join(self.site.target_path, fname[1:])
            else:
                source = os.path.join(self.source_path, fname)
                target = os.path.join(self.target_path, fname)
            self.site.echo('MEDIA', self.path, os.path.basename(fname))
            Site._makedirs(os.path.dirname(target))
            shutil.copyfile(source, target)

    def build_files(self):
        Site._makedirs(self.stage_path)
        globs = self.site.config.get('extra_files', self.path)
        if globs is not None:
            for glb in globs.split():
                # This won't do what it should if the path has anything
                # glob-like in it. Would be nice if glob() could take
                # a base path that isn't glob-interpreted.
                files = glob.glob(os.path.join(self.source_path, glb))
                for fname in files:
                    self.site.echo('FILE', self.path, os.path.basename(fname))
                    shutil.copyfile(fname,
                                    os.path.join(self.target_path,
                                                 os.path.basename(fname)))
        for subdir in self.directories:
            subdir.build_files()

    def build_feeds(self):
        atomfile = self.site.config.get('feed_atom', self.path)
        if atomfile is not None:
            self.site.echo('ATOM', self.path, atomfile)

            Site._makedirs(self.site.tools_path)
            for xsltfile in ('site2html.xsl', 'site2atom.xsl'):
                xsltpath = os.path.join(self.site.tools_path, xsltfile)
                if not os.path.exists(xsltpath):
                    from pkg_resources import resource_string
                    xsltcont = resource_string(__name__, xsltfile)
                    fd = open(xsltpath, 'w')
                    fd.write(codecs.decode(xsltcont, 'utf-8'))
                    fd.close()


            mal2xhtml = subprocess.check_output(['pkg-config',
                                                 '--variable', 'mal2xhtml',
                                                 'yelp-xsl'],
                                                universal_newlines=True)
            mal2xhtml = mal2xhtml.strip()
            if mal2xhtml == '':
                print('FIXME: mal2html not found')

            atomxsl = os.path.join(self.site.tools_path, 'pintail-atom.xsl')
            fd = open(atomxsl, 'w')
            fd.write('<xsl:stylesheet' +
                     ' xmlns:xsl="http://www.w3.org/1999/XSL/Transform"' +
                     ' version="1.0">\n')
            fd.write('<xsl:import href="' + mal2xhtml + '"/>\n')
            fd.write('<xsl:import href="site2atom.xsl"/>\n')
            html_extension = self.site.config.get('html_extension') or '.html'
            fd.write('<xsl:param name="html.extension" select="' +
                     "'" + html_extension + "'" + '"/>\n')
            link_extension = self.site.config.get('link_extension')
            if link_extension is not None:
                fd.write('<xsl:param name="mal.link.extension" select="' +
                         "'" + link_extension + "'" + '"/>\n')
            custom_xsl = self.site.config.get('custom_xsl')
            if custom_xsl is not None:
                custom_xsl = os.path.join(self.site.topdir, custom_xsl)
                fd.write('<xsl:include href="%s"/>\n' % custom_xsl)
            fd.write('</xsl:stylesheet>')
            fd.close()

            root = self.site.config.get('feed_root', self.path)
            if root is None:
                root = self.site.config.get('site_root') or '/'

            subprocess.call(['xsltproc',
                             '-o', os.path.join(self.target_path, atomfile),
                             '--stringparam', 'pintail.site.dir', self.path,
                             '--stringparam', 'pintail.site.root', root,
                             '--stringparam', 'feed.exclude_styles',
                             self.site.config.get('feed_exclude_styles', self.path) or '',
                             atomxsl, self.site.cache_path])
        for subdir in self.directories:
            subdir.build_feeds()


class EmptyDirectory(Directory):
    def read_directories(self):
        pass

    def read_pages(self):
        pass


class Site:
    def __init__(self, config):
        self.topdir = os.path.dirname(config)
        self.pindir = os.path.join(self.topdir, '__pintail__')
        self.stage_path = os.path.join(self.pindir, 'stage')
        self.target_path = os.path.join(self.pindir, 'build')
        self.tools_path = os.path.join(self.pindir, 'tools')

        self.cache_path = os.path.join(self.tools_path, 'pintail.cache')
        self.root = None
        self.config = Config(self, config)
        self.verbose = False

        self.yelp_xsl_branch = self.config.get('yelp_xsl_branch') or 'master'
        self.yelp_xsl_dir = 'yelp-xsl@' + self.yelp_xsl_branch.replace('/', '@')
        self.yelp_xsl_path = os.path.join(self.tools_path, self.yelp_xsl_dir)

    @classmethod
    def init_site(cls, directory):
        cfgfile = os.path.join(directory, 'pintail.cfg')
        if os.path.exists(cfgfile):
            sys.stderr.write('pintail.cfg file already exists\n')
            sys.exit(1)
        from pkg_resources import resource_string
        sample = resource_string(__name__, 'sample.cfg')
        fd = open(cfgfile, 'w')
        fd.write(codecs.decode(sample, 'utf-8'))
        fd.close()

    def read_directories(self):
        if self.root is not None:
            return
        if os.path.exists(self.stage_path):
            shutil.rmtree(self.stage_path)
        self.root = Directory(self, '/')
        directories = {'/': self.root}
        for directory in self.root.iter_directories():
            directories[directory.path] = directory

        configdirs = sorted(self.config.get_directories(), key=len)
        for path in configdirs:
            if path not in directories:
                directory = None
                for cls in Directory.iter_subclasses():
                    if cls.is_special_path(self, path):
                        directory = cls(self, path)
                        break
                if directory is None:
                    directory = EmptyDirectory(self, path)
                directories[path] = directory

                parentpath = '/'.join(path.split('/')[:-2]) + '/'
                if parentpath in directories:
                    directories[parentpath].directories.append(directory)
                    if directory.parent is None:
                        directory.parent = directories[parentpath]
                else:
                    while parentpath not in directories:
                        parentdir = EmptyDirectory(self, parentpath)
                        parentdir.directories.append(directory)
                        if directory.parent is None:
                            directory.parent = parentdir
                        directories[parentpath] = parentdir
                        directory = parentdir
                        parentpath = '/'.join(parentpath.split('/')[:-2]) + '/'
                    directories[parentpath].directories.append(directory)
                    if directory.parent is None:
                        directory.parent = directories[parentpath]

    def build(self):
        self.read_directories()
        self.build_cache()
        self.build_tools()
        self.build_html()
        self.build_media()
        self.build_css()
        self.build_js()
        self.build_files()
        self.build_icons()
        self.build_feeds()

    def build_cache(self):
        self.read_directories()
        self.echo('CACHE', '__tools__', 'pintail.cache')
        cache = etree.Element(CACHE_NS + 'cache', nsmap={
            None: 'http://projectmallard.org/1.0/',
            'cache': 'http://projectmallard.org/cache/1.0/',
            'site': 'http://projectmallard.org/site/1.0/'
        })
        for page in self.root.iter_pages():
            cdata = page.get_cache_data()
            if cdata is not None:
                cache.append(cdata)
        Site._makedirs(self.tools_path)
        cache.getroottree().write(self.cache_path,
                                  pretty_print=True)

    def build_tools(self):
        Site._makedirs(self.tools_path)
        if os.path.exists(self.yelp_xsl_path):
            if self.config._update:
                self.echo('UPDATE', 'https://git.gnome.org/browse/yelp-xsl', self.yelp_xsl_branch)
                p = subprocess.Popen(['git', 'pull', '-q', '-r', 'origin', self.yelp_xsl_branch],
                                     cwd=self.tools_path)
                p.communicate()
        else:
            self.echo('CLONE', 'https://git.gnome.org/browse/yelp-xsl', self.yelp_xsl_branch)
            p = subprocess.Popen(['git', 'clone', '-q',
                                  '-b', self.yelp_xsl_branch, '--single-branch',
                                  'https://git.gnome.org/browse/yelp-xsl',
                                  self.yelp_xsl_dir],
                                 cwd=self.tools_path)
            p.communicate()
        self.echo('BUILD', 'https://git.gnome.org/browse/yelp-xsl', self.yelp_xsl_branch)
        p = subprocess.Popen([os.path.join(self.yelp_xsl_path, 'autogen.sh')],
                             cwd=self.yelp_xsl_path,
                             stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL)
        p.communicate()
        p = subprocess.Popen(['make'], cwd=self.yelp_xsl_path, stdout=subprocess.DEVNULL)
        p.communicate()

        from pkg_resources import resource_string
        site2html = resource_string(__name__, 'pintail-html.xsl')
        fd = open(os.path.join(self.tools_path, 'pintail-html.xsl'),
                  'w', encoding='utf-8')
        fd.write(codecs.decode(site2html, 'utf-8'))
        fd.close()

        for cls in ToolsProvider.iter_subclasses():
            cls.build_tools(self)

    def build_html(self):
        self.read_directories()
        self.root.build_html()

    def build_media(self):
        self.read_directories()
        self.root.build_media()

    def build_css(self):
        self.read_directories()
        xslpath = os.path.join(self.yelp_xsl_path, 'xslt')

        Site._makedirs(self.tools_path)
        cssxsl = os.path.join(self.tools_path, 'pintail-css.xsl')
        fd = open(cssxsl, 'w')
        fd.writelines([
            '<xsl:stylesheet',
            ' xmlns:xsl="http://www.w3.org/1999/XSL/Transform"',
            ' xmlns:exsl="http://exslt.org/common"',
            ' xmlns:cache="http://projectmallard.org/cache/1.0/"',
            ' xmlns:mal="http://projectmallard.org/1.0/"',
            ' extension-element-prefixes="exsl"',
            ' version="1.0">\n',
            '<xsl:import href="' + xslpath + '/common/l10n.xsl"/>\n',
            '<xsl:import href="' + xslpath + '/common/color.xsl"/>\n',
            '<xsl:import href="' + xslpath + '/common/icons.xsl"/>\n',
            '<xsl:import href="' + xslpath + '/common/html.xsl"/>\n',
            '<xsl:import href="' + xslpath + '/mallard/html/mal2html-page.xsl"/>\n'
            ])
        custom_xsl = self.config.get('custom_xsl')
        if custom_xsl is not None:
            custom_xsl = os.path.join(self.topdir, custom_xsl)
            fd.write('<xsl:include href="%s"/>\n' % custom_xsl)
        fd.writelines([
            '<xsl:output method="text"/>\n',
            '<xsl:template match="/">\n',
            '<xsl:for-each select="/cache:cache/mal:page[\n',
            ' (@xml:lang and\n',
            '  not(@xml:lang = preceding-sibling::mal:page/@xml:lang)) or\n',
            ' (not(@xml:lang) and\n',
            '  not(preceding-sibling::mal:page[not(@xml:lang)])) ]">\n',
            '<xsl:variable name="locale">\n',
            ' <xsl:choose>\n',
            '  <xsl:when test="@xml:lang">\n',
            '   <xsl:value-of select="@xml:lang"/>\n',
            '  </xsl:when>\n',
            '  <xsl:otherwise>\n',
            '   <xsl:text>C</xsl:text>\n',
            '  </xsl:otherwise>\n',
            ' </xsl:choose>\n',
            '</xsl:variable>\n',
            '<exsl:document href="' + self.target_path + '/{$locale}.css" method="text">\n',
            ' <xsl:for-each select="document(@cache:href)">\n',
            '  <xsl:call-template name="html.css.content"/>\n',
            ' </xsl:for-each>\n',
            '</exsl:document>\n',
            '</xsl:for-each>\n',
            '</xsl:template>\n'
            '</xsl:stylesheet>\n'
            ])
        fd.close()

        # FIXME: need self.site.echo. Maybe we loop over the langs
        # in python, calling xsltproc for each.
        subprocess.call(['xsltproc',
                         '-o', self.target_path,
                         cssxsl, self.cache_path])

    def build_js(self):
        self.read_directories()
        jspath = os.path.join(self.yelp_xsl_path, 'js')

        if os.path.exists(os.path.join(jspath, 'jquery.js')):
            self.echo('JS', '/', 'jquery.js')
            shutil.copyfile(os.path.join(jspath, 'jquery.js'),
                            os.path.join(self.target_path, 'jquery.js'))

        xslpath = os.path.join(self.yelp_xsl_path, 'xslt')
        Site._makedirs(self.tools_path)

        jsxsl = os.path.join(self.tools_path, 'pintail-js.xsl')
        fd = open(jsxsl, 'w')
        fd.writelines([
            '<xsl:stylesheet',
            ' xmlns:xsl="http://www.w3.org/1999/XSL/Transform"',
            ' xmlns:exsl="http://exslt.org/common"',
            ' xmlns:cache="http://projectmallard.org/cache/1.0/"',
            ' xmlns:mal="http://projectmallard.org/1.0/"',
            ' extension-element-prefixes="exsl"',
            ' version="1.0">\n'
            '<xsl:import href="', xslpath, '/mallard/html/mal2xhtml.xsl"/>\n'
            ])
        custom_xsl = self.config.get('custom_xsl')
        if custom_xsl is not None:
            custom_xsl = os.path.join(self.topdir, custom_xsl)
            fd.write('<xsl:include href="%s"/>\n' % custom_xsl)
        fd.writelines([
            '<xsl:output method="text"/>\n',
            '<xsl:template match="/">\n',
            ' <xsl:call-template name="html.js.content"/>\n',
            '</xsl:template>\n',
            '</xsl:stylesheet>\n'
            ])
        fd.close()

        self.echo('JS', '/', 'yelp.js')
        subprocess.call(['xsltproc',
                         '-o', os.path.join(self.target_path, 'yelp.js'),
                         jsxsl, self.cache_path])

        if os.path.exists(os.path.join(jspath, 'highlight.pack.js')):
            self.echo('JS', '/', 'highlight.pack.js')
            shutil.copyfile(os.path.join(jspath, 'highlight.pack.js'),
                            os.path.join(self.target_path, 'highlight.pack.js'))

        if os.path.exists(os.path.join(jspath, 'jquery.syntax.js')):
            for js in ['jquery.syntax.js', 'jquery.syntax.core.js',
                       'jquery.syntax.layout.yelp.js']:
                self.echo('JS', '/', js)
                shutil.copyfile(os.path.join(jspath, js),
                                os.path.join(self.target_path, js))

            jsxsl = os.path.join(self.tools_path, 'pintail-js-brushes.xsl')
            fd = open(jsxsl, 'w')
            fd.writelines([
                '<xsl:stylesheet',
                ' xmlns:xsl="http://www.w3.org/1999/XSL/Transform"',
                ' xmlns:mal="http://projectmallard.org/1.0/"',
                ' xmlns:cache="http://projectmallard.org/cache/1.0/"',
                ' xmlns:exsl="http://exslt.org/common"',
                ' xmlns:html="http://www.w3.org/1999/xhtml"',
                ' extension-element-prefixes="exsl"',
                ' version="1.0">\n',
                '<xsl:import href="', xslpath, '/mallard/html/mal2xhtml.xsl"/>\n'
            ])
            custom_xsl = self.config.get('custom_xsl')
            if custom_xsl is not None:
                custom_xsl = os.path.join(self.topdir, custom_xsl)
                fd.write('<xsl:include href="%s"/>\n' % custom_xsl)
            fd.writelines([
                '<xsl:output method="text"/>\n',
                '<xsl:template match="/">\n',
                '<xsl:for-each select="/cache:cache/mal:page">\n',
                '<xsl:for-each select="document(@cache:href)//mal:code[@mime]">\n',
                '  <xsl:variable name="out">\n',
                '   <xsl:call-template name="mal2html.pre"/>\n',
                '  </xsl:variable>\n',
                '  <xsl:variable name="class">\n',
                '   <xsl:value-of select="exsl:node-set($out)/*/html:pre[last()]/@class"/>\n',
                '  </xsl:variable>\n',
                '  <xsl:if test="starts-with($class, ',
                "'contents syntax brush-'", ')">\n',
                '   <xsl:text>jquery.syntax.brush.</xsl:text>\n',
                '   <xsl:value-of select="substring-after($class, ',
                "'contents syntax brush-'", ')"/>\n',
                '   <xsl:text>.js&#x000A;</xsl:text>\n',
                '  </xsl:if>\n',
                '</xsl:for-each>\n',
                '</xsl:for-each>\n',
                '</xsl:template>\n',
                '</xsl:stylesheet>'
            ])
            fd.close()

            brushes = subprocess.check_output(['xsltproc',
                                               jsxsl, self.cache_path],
                                              universal_newlines=True)
            for brush in brushes.split():
                self.echo('JS', '/', brush)
                shutil.copyfile(os.path.join(jspath, brush),
                                os.path.join(self.target_path, brush))

    def build_files(self):
        self.read_directories()
        self.root.build_files()

    def build_feeds(self):
        self.read_directories()
        self.root.build_feeds()

    def build_icons(self):
        xslpath = subprocess.check_output(['pkg-config',
                                           '--variable', 'xsltdir',
                                           'yelp-xsl'],
                                          universal_newlines=True)
        xslpath = xslpath.strip()
        if xslpath == '':
            print('FIXME: yelp-xsl not found')

        xsl = (
            '<xsl:stylesheet'
            ' xmlns:xsl="http://www.w3.org/1999/XSL/Transform"'
            ' xmlns:mal="http://projectmallard.org/1.0/"'
            ' version="1.0">\n'
            '<xsl:import href="' + xslpath + '/common/icons.xsl"/>\n'
            )
        custom_xsl = self.config.get('custom_xsl')
        if custom_xsl is not None:
            custom_xsl = os.path.join(self.topdir, custom_xsl)
            xsl += ('<xsl:include href="%s"/>\n' % custom_xsl)
        xsl += (
            '<xsl:output method="text"/>\n'
            '<xsl:template match="/">\n'
            ' <xsl:value-of select="$icons.size.note"/>\n'
            '</xsl:template>\n'
            '</xsl:stylesheet>\n'
            )

        cmd = subprocess.Popen(['xsltproc', '-', self.cache_path],
                               stdin=subprocess.PIPE,
                               stdout=subprocess.PIPE)
        cmd.stdin.write(xsl.encode())
        cmd.stdin.close()
        iconsize = cmd.stdout.readline().decode()

        iconpath = subprocess.check_output(['pkg-config',
                                            '--variable', 'icondir',
                                            'yelp-xsl'],
                                           universal_newlines=True)
        iconpath = iconpath.strip()
        if iconpath == '':
            print('FIXME: yelp-xsl not found')

        iconpath = os.path.join(iconpath, 'hicolor',
                                iconsize + 'x' + iconsize,
                                'status')
        for f in os.listdir(iconpath):
            self.echo('ICON', '/', f)
            shutil.copyfile(os.path.join(iconpath, f),
                            os.path.join(self.target_path, f))

    def get_ignore_directory(self, directory):
        if directory == '/__pintail__/':
            return True
        # FIXME: use an ignore key in config
        if directory == '/.git/':
            return True
        return False

    def echo(self, tag, path, name):
        if self.verbose:
            print(tag + (' ' * (6 - len(tag))) + ' ' +
                  path.strip('/') + '/' + name)

    @classmethod
    def _makedirs(cls, path):
        # Python's os.makedirs complains if directory modes don't
        # match just so. I don't care if they match, as long as I
        # can write.
        if os.path.exists(path):
            return
        Site._makedirs(os.path.dirname(path))
        if not os.path.exists(path):
            os.mkdir(path)


class Config:
    def __init__(self, site, filename):
        self._site = site
        self._config = configparser.ConfigParser()
        self._config.read(filename)
        self._local = False
        self._update = True

    def get(self, key, path=None):
        if path is None:
            path = 'pintail'
        if self._local and path == 'pintail':
            ret = self._config.get('local', key, fallback=None)
            if ret is not None:
                return ret
        return self._config.get(path, key, fallback=None)

    def set_local(self):
        self._config.set('pintail', 'site_root',
                         self._site.target_path + '/')
        self._local = True

    def set_update(self, update):
        self._update = update

    def get_directories(self):
        return [d for d in self._config.sections()
                if d.startswith('/') and d.endswith('/')]