"""Read installed Vavoo modules in a private namespace; never modify its files."""
import ast
import importlib
import importlib.abc
import importlib.util
from pathlib import Path
import sys
import types
import xbmcaddon
import xbmcvfs

PREFIX='_nftv_vavoo'
VAVOO='plugin.video.vavoo.tv.plus'


class Imports(ast.NodeTransformer):
    def __init__(self,root):self.root=root
    def local(self,name):
        stem=name.split('.')[0]
        return (self.root/(stem+'.py')).is_file() or (self.root/stem).is_dir()
    def visit_ImportFrom(self,node):
        if not node.level and node.module and self.local(node.module):node.module=PREFIX+'.'+node.module
        return node
    def visit_Import(self,node):
        result=[]
        for alias in node.names:
            if not self.local(alias.name):
                result.append(ast.Import(names=[alias]));continue
            if alias.asname:
                result.append(ast.Import(names=[ast.alias(name=PREFIX+'.'+alias.name,asname=alias.asname)]))
            else:
                result.append(ast.Import(names=[ast.alias(name=PREFIX+'.'+alias.name)]))
                result.append(ast.ImportFrom(module=PREFIX,names=[ast.alias(name=alias.name.split('.')[0])],level=0))
        return result
    def visit_Call(self,node):
        self.generic_visit(node)
        # No-argument Addon() would otherwise pick Nebelflix TV's profile.
        if not node.args and not node.keywords:
            if ((isinstance(node.func,ast.Attribute) and isinstance(node.func.value,ast.Name)
                 and node.func.value.id=='xbmcaddon' and node.func.attr=='Addon')
                    or isinstance(node.func,ast.Name) and node.func.id=='Addon'):
                node.args=[ast.Constant(VAVOO)]
        return node


class Finder(importlib.abc.MetaPathFinder,importlib.abc.Loader):
    def __init__(self,root):self.root=Path(root).resolve()
    def find_spec(self,fullname,path=None,target=None):
        if not fullname.startswith(PREFIX+'.'):return None
        relative=fullname[len(PREFIX)+1:].replace('.', '/')
        directory=self.root/relative
        source=directory/'__init__.py' if directory.is_dir() else self.root/(relative+'.py')
        if not source.resolve().is_relative_to(self.root):raise ImportError('Invalid Vavoo module path')
        if not source.is_file() and not directory.is_dir():return None
        spec=importlib.util.spec_from_loader(fullname,self,is_package=directory.is_dir())
        spec.origin=str(source)
        return spec
    def create_module(self,spec):return None
    def exec_module(self,module):
        path=Path(module.__spec__.origin)
        module.__file__=str(path)
        if module.__spec__.submodule_search_locations is not None:module.__path__=[str(path.parent)]
        if not path.is_file():return
        tree=Imports(self.root).visit(ast.parse(path.read_text(encoding='utf-8-sig')))
        ast.fix_missing_locations(tree)
        exec(compile(tree,str(path),'exec'),module.__dict__)


def load():
    addon=xbmcaddon.Addon(VAVOO)
    root=Path(xbmcvfs.translatePath(addon.getAddonInfo('path')))/'resources/lib'
    if PREFIX not in sys.modules:
        package=types.ModuleType(PREFIX);package.__path__=[str(root)]
        sys.modules[PREFIX]=package;sys.meta_path.insert(0,Finder(root))
    return importlib.import_module(PREFIX+'.vavoo_api'),importlib.import_module(PREFIX+'.group_favs')
