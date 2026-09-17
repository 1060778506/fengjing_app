"""Local Chromium UI regression only; does not access ERPNext/Ozon or write records."""
from pathlib import Path
from playwright.sync_api import sync_playwright


def run():
    base = Path(__file__).parent
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True, args=["--no-sandbox"])
        page = browser.new_page(viewport={"width": 1600, "height": 1000})
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.set_content("<html><body></body></html>")
        page.add_script_tag(content=(base / "ozon_freight_calcula.js").read_text() + "\nwindow.TestCanvas=OzFreightCanvas;")
        page.add_style_tag(content=(base / "ozon_freight_calcula.css").read_text())
        page.evaluate("""() => {
          window.frappe={msgprint:console.log,show_alert:()=>{}};
          if(!crypto.randomUUID)crypto.randomUUID=()=> 'test-copy-'+Math.random().toString(16).slice(2);
          const c=window.canvas=Object.create(TestCanvas.prototype);
          c.root=document.createElement('div');c.root.className='ozfc';document.body.append(c.root);
          c.s={nodes:[{id:'ui-test',manual:true,manualSale:100,commissionOverride:10,item:{item_code:'SIM-ui',item_name:'测试物料',length:100,width:100,height:100,weight:200,value:10,value_currency:'CNY',quantity:4},x:0,y:0}],active:'ui-test',focus:'ui-test',expanded:['ui-test'],exclusive:true,view:{x:0,y:0,z:1}};
          c.c={cny_per_rub:.08,margin_pct:35,routes:[{id:'ui-route',enabled:true,provider:'测试',name:'按包计费',speed:'Standard',fixed:5,rate:10,min_weight:0,max_weight:20,min_value:0,max_value:10000,max_side:100,max_sum:200,divisor:12000,step:.001}]};
          c.undo=[];c.redo=[];c.shell();c.bind();c.paint();
        }""")
        assert page.locator(".fc-packing").count() == 1
        assert page.evaluate("""async () => {
          const c=canvas,api=c.api,save=c.save,hydrate=c.hydrateHistory;let calls=0,hydrations=0,delay;
          const interval=window.setInterval;window.setInterval=(fn,ms)=>{delay=ms;return 123;};c.startAutoSave();window.setInterval=interval;c.autoSaveTimer=null;
          c.autosaveReady=true;c.api=async()=>{calls++;return {name:'local-test',modified:'local-revision'};};c.hydrateHistory=async()=>{hydrations++;};
          c.dirty=false;if(await c.autoSaveTick()||calls)return false;
          c.dirty=true;c.saving=true;if(await c.autoSaveTick()||calls)return false;c.saving=false;
          const input=document.querySelector('[data-f="value"]');input.focus();if(await c.autoSaveTick()||calls)return false;input.blur();
          const modal=document.createElement('div');modal.className='modal show';document.body.append(modal);if(await c.autoSaveTick()||calls)return false;modal.remove();
          const before=document.querySelector('.fc-item'),saved=await c.autoSaveTick();
          const valid=saved&&delay===60000&&calls===1&&!c.dirty&&hydrations===0&&before===document.querySelector('.fc-item');
          c.save=async()=>{throw Error('offline')};c.dirty=true;const failed=await c.autoSaveTick();const retained=c.dirty&&!c.autoSaving;
          c.api=api;c.save=save;c.hydrateHistory=hydrate;c.name=null;c.modified=null;
          return valid&&!failed&&retained;
        }"""), 'One-minute autosave guards, quiet saving without paint, failed save retains edits'
        assert page.locator(".fc-quotes").count() == 1
        assert page.locator('.fc-route .fc-suggested-route.ready').count() == 1
        assert page.locator('.fc-best-validation .fc-suggested-route.ready').count() == 1
        assert page.locator('.fc-route summary .fc-suggested-route').count() == 1
        assert page.locator('[data-logistics-filter="destination"]').input_value() == '俄罗斯'
        assert page.locator('[data-logistics-filter="mode"]').input_value() == 'RFBS'
        page.evaluate("canvas.copyItem=async value=>{window.lastCopied=value}")
        page.locator('.fc-copy-name').click()
        assert page.evaluate('window.lastCopied') == '测试物料'
        assert page.locator('[data-logistics-speed="Standard"]').is_checked()
        page.locator('[data-logistics-speed="Standard"]').uncheck()
        page.locator('[data-logistics-speed="Express"]').check()
        assert page.locator('.fc-route').count() == 0, 'Unclassified routes do not match an explicit speed'
        page.locator('[data-logistics-speed="Express"]').uncheck()
        assert page.locator('.fc-route').count() == 1
        toggle=page.locator('.fc-controls button.fc-exclusive')
        assert toggle.get_attribute('aria-pressed') == 'true'
        assert page.locator('input.fc-exclusive').count() == 0
        toggle.click()
        assert toggle.get_attribute('aria-pressed') == 'false'
        assert page.evaluate('canvas.s.exclusive') is False
        toggle.click()
        assert toggle.get_attribute('aria-pressed') == 'true'
        assert page.evaluate('canvas.s.exclusive') is True
        page.evaluate("canvas.resetFlowPositions(canvas.s.nodes[0]);canvas.paint()")
        assert page.evaluate("document.querySelector('.fc-cost-card').getBoundingClientRect().right <= document.querySelector('.fc-sale-card').getBoundingClientRect().left"), "Cost and sale cards must not overlap"
        page.locator("[data-pack-mode]").select_option("equal")
        page.locator("[data-pack-size]").fill("2")
        page.locator("[data-pack-size]").dispatch_event("change")
        assert page.locator(".fc-quotes").count() == 2, "Equal split creates two logistics cards"
        assert page.evaluate("canvas.combinedFreight(canvas.s.nodes[0]).price") == 18
        assert page.evaluate("""() => {
          const c=canvas,n=c.s.nodes[0],zoom=c.s.view.z;
          const card=document.querySelector('.fc-packing').getBoundingClientRect();
          const paths=[...document.querySelectorAll('.fc-lines path[stroke="#6b7bf5"]')];
          return paths.length===2 && paths.every((path,i)=>{
            const row=document.querySelector('[data-pack-row="'+i+'"]').getBoundingClientRect();
            const y=Number(path.getAttribute('d').split(' ')[1]);
            return Math.abs(card.top+(y-c.flowPositions(n).packing.y)*zoom-(row.top+row.height/2))<1;
          }) && document.querySelectorAll('.fc-lines circle').length===document.querySelectorAll('.fc-lines path').length*2;
        }"""), "Each package line starts at its own row, every edge has two dots"
        page.locator("[data-pack-mode]").select_option("custom")
        page.evaluate("""() => {const el=document.querySelector('[data-f="value"]');el.focus();el.value='1000';el.dispatchEvent(new Event('input',{bubbles:true}));}""")
        page.wait_for_timeout(250)
        assert page.locator('.fc-route .fc-suggested-route.blocked').count() == 2, 'Cost edits update recommended-price restrictions'
        assert page.locator('.fc-best-validation .fc-suggested-route.blocked').count() == 2
        page.evaluate("""() => {const el=document.querySelector('[data-f="value"]');el.value='10';el.dispatchEvent(new Event('input',{bubbles:true}));el.blur();}""")
        page.wait_for_timeout(250)
        assert page.locator('.fc-route .fc-suggested-route.ready').count() == 2
        assert page.locator('.fc-best-validation .fc-suggested-route.ready').count() == 2
        quantity=page.locator('[data-pack-node="ui-test"][data-pack-index="0"][data-pack-field="quantity"]')
        quantity.fill("3")
        page.wait_for_timeout(250)
        assert page.locator('[data-pack-node="ui-test"][data-pack-index="0"][data-pack-field="weight"]').input_value() == "600", "Package weight follows quantity while typing"
        assert quantity.evaluate("el=>el===document.activeElement"), "Quantity input stays focused"
        quantity.fill("2")
        page.wait_for_timeout(250)
        page.locator("[data-pack-add]").click()
        assert page.locator(".fc-quotes").count() == 3, "Adding a package adds a logistics card"
        assert page.evaluate("canvas.combinedFreight(canvas.s.nodes[0]) === undefined"), "Mismatched quantities must not produce a price"
        page.locator("[data-pack-delete]").last.click()
        page.evaluate("""() => {const el=document.querySelector('[data-f="value"]');el.focus();el.value='123.45';el.setSelectionRange(3,3);el.dispatchEvent(new Event('input',{bubbles:true}));window.savedInput=el;}""")
        page.wait_for_timeout(250)
        assert page.evaluate("savedInput===document.activeElement && savedInput.selectionStart===3"), "Input element/caret survives calculation"
        page.evaluate("document.activeElement.blur()")
        page.keyboard.press("Control+c")
        assert page.evaluate("!!canvas.cardClipboard"), "Copy shortcut captures selected material"
        page.keyboard.press("Control+v")
        page.wait_for_timeout(100)
        assert page.evaluate("canvas.s.nodes.length") == 2, ("Paste creates a card", errors)
        assert page.locator(".fc-item").count() == 2
        page.evaluate("document.querySelector('[data-f=\"value\"]').focus()")
        page.keyboard.press('Delete')
        assert page.evaluate('canvas.s.nodes.length') == 2, 'Delete in input must not remove cards'
        page.evaluate('document.activeElement.blur()')
        page.keyboard.press('Delete')
        assert page.evaluate('canvas.s.nodes.length') == 1, 'Delete removes selected material and child cards'
        page.evaluate('canvas.history(true)')
        assert page.evaluate('canvas.s.nodes.length') == 2, 'Undo restores material and child settings'
        page.evaluate("canvas.s.nodes[1].packing.rows[0].weight=999")
        assert page.evaluate("canvas.s.nodes[0].packing.rows[0].weight") == 400, "Copied settings are independent"
        assert not errors, errors
        page.evaluate("canvas.s.displayMode='table';canvas.paint()")
        assert page.locator(".fc-table-packages").count() == 2
        assert page.locator(".fc-packing").count() == 2
        assert page.locator(".fc-quotes").count() == 4
        page.evaluate("canvas.s.displayMode='canvas';canvas.paint()")
        assert not errors, errors
        print("PASS: Chromium UI: package cards, quantity guard, input caret, Ctrl+C/Ctrl+V deep copy")
        page.evaluate("canvas.toggleMultiSelect();canvas.selectedNodes=new Set(canvas.s.nodes.map(n=>n.id));canvas.paint();document.activeElement.blur()")
        page.keyboard.press('Delete')
        assert page.evaluate('canvas.s.nodes.length') == 0, 'Delete removes all selected materials'
        page.evaluate('canvas.history(true)')
        assert page.evaluate('canvas.s.nodes.length') == 2
        assert not errors, errors
        browser.close()


if __name__ == "__main__":
    run()
