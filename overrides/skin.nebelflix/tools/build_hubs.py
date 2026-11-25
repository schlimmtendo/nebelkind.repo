"""Compile one shared control layout and twelve independent Kodi windows."""
from copy import deepcopy
from pathlib import Path
import re
import sys
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'resources'))
import hub_protocol


def write(tree, path):
    ET.indent(tree, space='  ')
    tree.write(path, encoding='utf-8', xml_declaration=True)


def build():
    template = ET.parse(ROOT/'tools/templates/hub-window.xml')
    controls = template.getroot().find('controls')
    # Keep preparation as its own fullscreen dialog, independent of hub content.
    original=next(node for node in controls if 'NF.PrepareWidgets' in node.findtext('visible',''))
    overlay=deepcopy(original)
    for visible in list(overlay.findall('visible')):overlay.remove(visible)
    preparation=ET.Element('window',{'type':'dialog','id':'1194'})
    ET.SubElement(preparation,'onunload').text='SetProperty(NF.SkipPreload,true,Home)'
    ET.SubElement(preparation,'zorder').text='10'
    container=ET.SubElement(preparation,'controls');container.append(overlay)
    write(ET.ElementTree(preparation),ROOT/'xml/Custom_1194_PrepareWidgets.xml')
    wizard=ET.Element('window',{'type':'dialog','id':'1195'})
    ET.SubElement(wizard,'defaultcontrol',{'always':'true'}).text='1900'
    ET.SubElement(wizard,'onunload').text='SetProperty(NF.SetupClosed,true,Home)'
    ET.SubElement(wizard,'zorder').text='11'
    wc=ET.SubElement(wizard,'controls')
    def make_control(parent,kind,fields,cid=None):
        c=ET.SubElement(parent,'control',dict(type=kind,**({'id':str(cid)} if cid is not None else {})))
        for tag,value in fields.items():ET.SubElement(c,tag).text=str(value)
        return c
    bg=make_control(wc,'image',dict(left=0,top=0,width=1920,height=1080,texture='nebelflix/white.png'))
    bg.find('texture').set('colordiffuse','FF141414')
    make_control(wc,'image',dict(left=110,top=70,width=550,height=125,texture='nebelflix/widget-preload-logo.png',aspectratio='keep'))
    make_control(wc,'label',dict(left=110,top=220,width=1600,height=45,font='NFSmall',textcolor='FFE50914',label='EINRICHTUNG · SCHRITT $INFO[Window(Home).Property(NF.SetupStep)] VON 3'))
    make_control(wc,'label',dict(left=110,top=285,width=1690,height=70,font='NFHeading',textcolor='FFFFFFFF',label='$INFO[Window(Home).Property(NF.SetupTitle)]'))
    make_control(wc,'textbox',dict(left=110,top=380,width=1660,height=270,font='NFBody',textcolor='FFDDDDDD',label='$INFO[Window(Home).Property(NF.SetupBody)]'))
    for index in range(3):
        c=make_control(wc,'button',dict(left=200,top=675+index*90,width=1520,height=72,font='NFBody',
            label='$INFO[Window(Home).Property(NF.SetupOption%s)]'%index,
            visible='Integer.IsGreater(Window(Home).Property(NF.SetupCount),%s)'%index,
            texturefocus='nebelflix/button-focus.png',texturenofocus='nebelflix/pill-dark.png',
            textcolor='FFFFFFFF',focusedcolor='FF141414',disabledcolor='FFDDDDDD',align='center',aligny='center',textoffsetx=0,textoffsety=0,
            onup=str(1900+max(0,index-1)),onleft='1900',onright='1900'),1900+index)
        for step in (1,2,3):
            ET.SubElement(c,'onclick',{'condition':'String.IsEqual(Window(Home).Property(NF.SetupInputReady),true) + String.IsEqual(Window(Home).Property(NF.SetupStep),%s)'%step}).text='SetProperty(NF.SetupChoice,%s:%s,Home)'%(step,index)
        ET.SubElement(c,'ondown',{'condition':'Integer.IsGreater(Window(Home).Property(NF.SetupCount),%s)'%(index+1)}).text=str(1900+min(2,index+1))
        ET.SubElement(c,'ondown',{'condition':'!Integer.IsGreater(Window(Home).Property(NF.SetupCount),%s)'%(index+1)}).text='1900'
    write(ET.ElementTree(wizard),ROOT/'xml/Custom_1195_Setup.xml')
    # Remove controller-owned spotlight feeds, global preload overlays and gate.
    for control in list(controls):
        visible = control.findtext('visible', '')
        if (control.find(".//control[@id='650']") is not None or
                any(name in visible for name in ('NF.PrepareWidgets','NF.BackgroundCacheNotice','NF.HubSwitching'))):
            controls.remove(control)
    body = controls.find(".//control[@id='500']")
    for animation in list(body.findall('animation')):
        body.remove(animation)
    for group in body.findall('control'):
        visible = group.find('visible')
        row_list = next((node for node in group.findall('control')
                         if node.get('type')=='list' and 600 <= int(node.get('id','0')) < 648), None)
        if row_list is None:
            if visible is not None and 'NF.HasHero' in visible.text:
                visible.text += ' + String.IsEqual(Window(Home).Property(NF.SelectedPage),Window.Property(NF.Page)) + Integer.IsEqual(Window.Property(NF.Segment),0)'
            continue
        cid = row_list.get('id')
        # The controller fills native lists in one process. An empty dynamic
        # content provider would otherwise erase restored native items.
        for content in list(row_list.findall('content')):row_list.remove(content)
        # Each static item carries its exact index; a container onclick would
        # override it and could resolve an ambiguous CurrentItem label.
        for action in list(row_list.findall('onclick')):row_list.remove(action)
        content=ET.SubElement(row_list,'content')
        for index in range(hub_protocol.STATIC_TILE_LIMIT):
            item=ET.SubElement(content,'item',{'id':str(index+1)})
            prefix='NF.Tile%s.%s.'%(cid,index)
            ET.SubElement(item,'visible').text='Integer.IsGreater(Window.Property(NF.TileCount%s),%s)'%(cid,index)
            ET.SubElement(item,'label').text='$INFO[Window.Property(%sTitle)]'%prefix
            ET.SubElement(item,'thumb').text='$INFO[Window.Property(%sThumb)]'%prefix
            ET.SubElement(item,'icon').text='$INFO[Window.Property(%sThumb)]'%prefix
            for field in hub_protocol.STATIC_TILE_PROPERTIES:
                ET.SubElement(item,'property',{'name':field}).text='$INFO[Window.Property(%s%s)]'%(prefix,field)
            ET.SubElement(item,'onclick').text='RunScript(skin.nebelflix,details,%s,%s,$INFO[Window.Property(NF.Page)])'%(cid,index+1)
        for layout in list(row_list.findall('itemlayout'))+list(row_list.findall('focusedlayout')):
            holder=layout.find('control')
            if holder is None:holder=layout
            top10=int(layout.get('width','286'))>300
            left=130 if top10 else 4
            badge=ET.SubElement(holder,'control',{'type':'label'})
            for key,value in dict(left=str(left+222),top='12',width='42',height='34',font='NFBody',align='center',aligny='center',label='✓',textcolor='FF35D875',visible='String.IsEqual(ListItem.Property(NF.WatchState),watched)').items():ET.SubElement(badge,key).text=value
            bar=ET.SubElement(holder,'control',{'type':'image'})
            for key,value in dict(left=str(left),top='153',width='270',height='6',texture='$INFO[ListItem.Property(NF.WatchBar)]',visible='String.IsEqual(ListItem.Property(NF.WatchState),partial)').items():ET.SubElement(bar,key).text=value
        for element in row_list.iter():
            if element.text:element.text=element.text.replace('ListItem.Art(clearlogo)','ListItem.Property(NF.Clearlogo)')
            for key,value in list(element.attrib.items()):
                element.set(key,value.replace('ListItem.Art(clearlogo)','ListItem.Property(NF.Clearlogo)'))

        # Auto-size the title so the spinner sits beside its text, not over it.
        title=next(node for node in group.findall('control')
                   if 'NF.RowHeading' in (node.findtext('label') or ''))
        group.remove(title)
        heading=ET.Element('control',{'type':'grouplist'})
        for tag,value in dict(left='64',top='0',width='1500',height='48',orientation='horizontal',itemgap='12').items():
            ET.SubElement(heading,tag).text=value
        title.find('left').text='0';title.find('width').text='auto'
        title.find('width').set('max','1100');heading.append(title)
        spinner=ET.SubElement(heading,'control',{'type':'image'})
        for tag,value in dict(width='30',height='30',top='9',texture='spinner.png',
            visible='String.IsEqual(Window.Property(NF.RowLoading%s),true) | Container(%s).IsUpdating'%(cid,cid)).items():
            ET.SubElement(spinner,tag).text=value
        ET.SubElement(spinner,'animation',{'effect':'rotate','start':'0','end':'360','center':'auto',
            'time':'1000','loop':'true','condition':'true'}).text='Conditional'
        group.insert(0,heading)

        for animation in list(group.findall('animation')):
            group.remove(animation)
        enabled = ('!String.IsEmpty(Window.Property(NF.RowHeading%s))' % cid
                   + ' + [!Skin.HasSetting(NFHideContinue) | !String.IsEqual(Window.Property(NF.RowKind%s),continue)]' % cid
                   + ' + [!Skin.HasSetting(NFHideTop10) | !String.IsEqual(Window.Property(NF.RowKind%s),top10)]' % cid)
        # Static providers cannot populate while their parent is hidden. Use
        # the committed snapshot count to avoid a NumItems/visibility deadlock.
        visible.text = enabled + (' + [Integer.IsGreater(Window.Property(NF.TileCount%s),0) | Container(%s).IsUpdating | Integer.IsGreater(Container(%s).NumItems,0) | String.IsEqual(Window.Property(NF.RowLoading%s),true)]' % (cid,cid,cid,cid))
        # Back always returns to the active topbar tab, including empty personal rows.
        for action in list(row_list.findall('onback')):row_list.remove(action)
        ET.SubElement(row_list,'onback').text='SetFocus($INFO[Window.Property(NF.TopbarControl)])'
        # Like Arctic's focus helpers: a remote can reach a loading row even
        # before Kodi has a first list item. It occupies that row, not the topbar.
        pending=deepcopy(row_list);pending.set('type','button');pending.set('id',str(1600+int(cid)-600))
        for node in list(pending):
            if node.tag not in ('left','top','width','height','onup','ondown','onleft','onback','onfocus'):
                pending.remove(node)
        ET.SubElement(pending,'visible').text='String.IsEqual(Window.Property(NF.RowLoading%s),true) + Integer.IsEqual(Container(%s).NumItems,0)'%(cid,cid)
        ET.SubElement(pending,'label').text=''
        ET.SubElement(pending,'texturefocus').text='nebelflix/button-focus.png'
        ET.SubElement(pending,'texturenofocus').text=''
        group.append(pending)
        label = ET.SubElement(group,'control',{'type':'label'})
        for tag,value in dict(left='64',top='205',width='1600',height='44',font='NFSmall',
                              textcolor='FFAAAAAA',label='Inhalte werden geladen …',
                              visible='String.IsEqual(Window.Property(NF.RowLoading%s),true) + Integer.IsEqual(Container(%s).NumItems,0)' % (cid,cid)).items():
            ET.SubElement(label,tag).text=value
        for left in (64,350,636):
            skeleton=ET.SubElement(group,'control',{'type':'image'})
            for tag,value in dict(left=str(left),top='44',width='270',height='152',
                texture='nebelflix/white.png',colordiffuse='FF242429',
                visible='String.IsEqual(Window.Property(NF.RowLoading%s),true) + Integer.IsEqual(Container(%s).NumItems,0)'%(cid,cid)).items():
                if tag=='colordiffuse':
                    skeleton.find('texture').set('colordiffuse',value)
                else:ET.SubElement(skeleton,tag).text=value
    # Only row configuration is local; global title/action state stays on Home.
    text = ET.tostring(controls,encoding='unicode')
    text = re.sub(r'Window\(Home\)\.Property\(NF\.(RowURL|RowHeading|RowState|RowKind|RowsPage)(\d*)\)',
                  r'Window.Property(NF.\1\2)',text)
    text = text.replace('String.IsEqual(Window.Property(NF.RowsPage),Window(Home).Property(NF.Page)) + ','')
    text = text.replace('$INFO[Window(Home).Property(NF.Page)]','$INFO[Window.Property(NF.Page)]')
    # Rows belong to this window; navigation must not depend on the previous hub.
    text = re.sub(r'String.IsEqual\(Window.Property\(NF.RowsPage\),Window\(Home\).Property\(NF.Page\)\) \+ ', '', text)
    text = text.replace('String.IsEqual(Window(Home).Property(NF.HasHero),true)', 'String.IsEqual(Window(Home).Property(NF.HasHero),true) + !Skin.HasSetting(NFHideSpotlight)')
    controls = ET.fromstring(text)
    for node in controls.findall('.//control'):
        if node.get('type') not in ('button','list'):continue
        for action in node.findall('onup')+node.findall('ondown')+node.findall('onleft'):
            if not action.get('condition') and action.text in ('200','212'):
                action.text='SetFocus($INFO[Window.Property(NF.TopbarControl)])'

    for slot in range(12):
        button=controls.find(".//control[@id='%s']"%hub_protocol.topbar_id(slot))
        if button is None:continue
        for direction,indices,fallback in (('onleft',range(slot-1,-1,-1),212),('onright',range(slot+1,12),210)):
            for action in list(button.findall(direction)):button.remove(action)
            for index in indices:
                ET.SubElement(button,direction,{'condition':'!String.IsEmpty(Window(Home).Property(NF.TabLabel%s))'%index}).text=str(hub_protocol.topbar_id(index))
            ET.SubElement(button,direction).text=str(fallback)
        for action in list(button.findall('onclick')):button.remove(action)
        ET.SubElement(button,'onclick').text='SetProperty(NF.StopSpotlight,true,Home)'
        ET.SubElement(button,'onclick').text='ClearProperty(NF.HubRequestedAt,Home)'
        ET.SubElement(button,'onclick').text='ActivateWindow($INFO[Window(Home).Property(NF.TabWindow%s)])'%slot
    for node in controls.iter():
        if node.tag in ('onup','ondown') and node.get('condition'):
            node.set('condition',re.sub(r'Control.IsVisible\((6[0-4][0-9])\)',r'Control.IsVisible(\1) + Integer.IsGreater(Container(\1).NumItems,0)',node.get('condition')))
    for node in list(controls.iter()):
        for action in list(node):
            if action.tag not in ('onup','ondown'):continue
            target=action.text or ''
            if target.isdigit() and 600<=int(target)<648 and action.get('condition'):
                loading=deepcopy(action)
                condition=re.sub(r' \+ Integer.IsGreater\(Container\(\d+\).NumItems,0\)', '',action.get('condition'))
                loading.set('condition',condition+' + Integer.IsEqual(Container(%s).NumItems,0) + String.IsEqual(Window.Property(NF.RowLoading%s),true)'%(target,target))
                loading.text=str(1600+int(target)-600)
                node.insert(list(node).index(action)+1,loading)
    body=controls.find(".//control[@id='500']")
    ET.SubElement(body,'visible').text='String.IsEqual(Window.Property(NF.RowsReady),true) + !String.IsEqual(Window(Home).Property(NF.StartupBlocked),true)'
    segments=[ET.Element('include',{'name':'NFHubRows'+str(i)}) for i in range(hub_protocol.VIEWS_PER_HUB)]
    for group in list(body):
        row_list=next((node for node in group.findall('control') if node.get('type')=='list' and 600<=int(node.get('id','0'))<648),None)
        if row_list is not None:
            body.remove(group);segments[(int(row_list.get('id'))-600)//hub_protocol.ROWS_PER_VIEW].append(group)
    ET.SubElement(body,'include',{'content':'NFHubRows$PARAM[segment]'})
    empty_group=ET.SubElement(body,'control',{'type':'group'})
    ET.SubElement(empty_group,'height').text='120'
    ET.SubElement(empty_group,'visible').text='String.IsEqual(Window.Property(NF.Empty),true)'
    empty = ET.SubElement(empty_group,'control',{'type':'label'})
    for tag,value in dict(left='180',top='0',width='1560',height='120',font='NFBody',align='center',
                          label='Für diesen Bereich sind derzeit keine Inhalte vorhanden.',
                          visible='String.IsEqual(Window.Property(NF.Empty),true)').items():
        ET.SubElement(empty,tag).text=value
    gate=ET.SubElement(controls,'control',{'type':'group'})
    ET.SubElement(gate,'visible').text='String.IsEqual(Window(Home).Property(NF.StartupBlocked),true)'
    bg=make_control(gate,'image',dict(left=0,top=0,width=1920,height=1080,texture='nebelflix/white.png'))
    bg.find('texture').set('colordiffuse','FF141414')
    make_control(gate,'image',dict(left=610,top=300,width=700,height=170,texture='nebelflix/widget-preload-logo.png',aspectratio='keep'))
    make_control(gate,'label',dict(left=200,top=535,width=1520,height=70,font='NFHeading',textcolor='FFFFFFFF',align='center',label='Dein Nebelflix wird eingerichtet'))
    make_control(gate,'label',dict(left=200,top=625,width=1520,height=55,font='NFBody',textcolor='FFDDDDDD',align='center',label='Zuerst Einrichtung und Vorbereitung – danach erscheinen deine Bereiche.'))
    launch=make_control(gate,'button',dict(left=660,top=740,width=600,height=70,font='NFBody',label='$VAR[NFSetupLaunchLabel]',align='center',aligny='center',textoffsetx=0,textoffsety=0,textcolor='FFFFFFFF',focusedcolor='FF141414',disabledcolor='FFBBBBBB',texturefocus='nebelflix/button-focus.png',texturenofocus='nebelflix/pill-dark.png',onup='1899',ondown='1899',onleft='1899',onright='1899'),1899)
    ET.SubElement(launch,'onclick',{'condition':'!String.IsEqual(Window(Home).Property(NF.SetupLaunchRequested),true)'}).text='RunScript(special://skin/resources/bootstrap.py)'
    ET.SubElement(launch,'onclick').text='SetProperty(NF.SetupLaunchRequested,true,Home)'
    includes = ET.Element('includes')
    launch_label=ET.SubElement(includes,'variable',{'name':'NFSetupLaunchLabel'})
    ET.SubElement(launch_label,'value',{'condition':'String.IsEqual(Window(Home).Property(NF.SetupLaunchRequested),true)'}).text='Einrichtung wird geöffnet …'
    ET.SubElement(launch_label,'value').text='Einrichtung fortsetzen'
    include = ET.SubElement(includes,'include',{'name':'NFHubControls'})
    ET.SubElement(include,'param',{'name':'segment','default':'0'})
    definition=ET.SubElement(include,'definition')
    for node in controls:
        definition.append(deepcopy(node))
    for segment in segments:includes.append(segment)
    write(ET.ElementTree(includes),ROOT/'xml/Includes_NFHub.xml')
    aggregate = ET.parse(ROOT/'xml/Includes.xml')
    if aggregate.find("include[@file='Includes_NFHub.xml']") is None:
        ET.SubElement(aggregate.getroot(),'include',{'file':'Includes_NFHub.xml'})
    write(aggregate,ROOT/'xml/Includes.xml')
    valid={hub_protocol.window_id(slot,segment) for slot in range(12) for segment in range(hub_protocol.VIEWS_PER_HUB)}
    directory=(ROOT/'xml').resolve()
    for old in directory.glob('Custom_*_NFHub.xml'):
        if old.resolve().parent!=directory or old.is_symlink():raise ValueError('Unsafe generated XML path')
        if int(old.name.split('_')[1]) not in valid:old.unlink()
    for slot in range(12):
        for segment in range(hub_protocol.VIEWS_PER_HUB):
            home=slot==0 and segment==0
            wid=hub_protocol.window_id(slot,segment)
            window = ET.Element('window',{} if home else {'type':'window','id':str(wid)})
            ET.SubElement(window,'defaultcontrol',{'always':'false'}).text=str(hub_protocol.topbar_id(slot))
            ET.SubElement(window,'backgroundcolor').text='FF141414'
            ET.SubElement(window,'onload').text='SetProperty(NF.HubSlot,%s)' % slot
            ET.SubElement(window,'onload').text='SetProperty(NF.Segment,%s)' % segment
            ET.SubElement(window,'onload').text='SetProperty(NF.TopbarControl,%s)' % hub_protocol.topbar_id(slot)
            condition='[String.IsEmpty(Window(Home).Property(NF.Heartbeat)) + String.IsEmpty(Window(Home).Property(NF.ControllerStarting))]'
            if not home:condition='String.IsEmpty(Window.Property(NF.LayoutSignature)) | '+condition
            ET.SubElement(window,'onload',{'condition':condition}).text=('RunScript(special://skin/resources/bootstrap.py)' if home else
                'RunScript(special://skin/resources/bootstrap.py,hub,%s,%s)'%(slot,segment))
            content = ET.SubElement(window,'controls')
            ref=ET.SubElement(content,'include',{'content':'NFHubControls'})
            ET.SubElement(ref,'param',{'name':'segment'}).text=str(segment)
            name = 'Home.xml' if home else 'Custom_%s_NFHub.xml' % wid
            write(ET.ElementTree(window),ROOT/'xml'/name)
    settings = ET.parse(ROOT/'xml/Custom_1190_NebelflixSettings.xml')
    button = settings.find(".//control[@id='1212']")
    button.find('label').text='Einrichtung & Widgets verwalten'
    button.find('onclick').text='RunScript(skin.nebelflix,setup_menu)'
    write(settings,ROOT/'xml/Custom_1190_NebelflixSettings.xml')
    print('Built 12 independent hubs, with continuous widget rows and remote navigation')
    startup=ET.Element('window')
    ET.SubElement(startup,'onload').text='SetProperty(NF.StartupIntroPending,true,Home)'
    ET.SubElement(startup,'onload').text='RunScript(special://skin/resources/bootstrap.py)'
    ET.SubElement(startup,'controls')
    write(ET.ElementTree(startup),ROOT/'xml/Startup.xml')


if __name__=='__main__':
    build()
