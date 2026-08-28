from __future__ import annotations

import json
import re
import shutil
import subprocess

from taskplane.dashboard import (
    HOST_DASHBOARD_COMPONENTS,
    _TEMPLATE,
    _WIDGET_JS,
    _widget_detail_panels,
    _widget_tabs,
    native_dashboard_projection,
    render_native_dashboard_surface,
)
from taskplane.host_native import HostSurfaceSnapshot


def _snapshot(*, item_count=9, actions=("approve", "inspect", "export")):
    items = [
        {"id": f"F-{index:02d}", "title": f"Finding {index}",
         "severity": "high" if index == 0 else "low"}
        for index in range(item_count)
    ]
    values = {
        name: {
            "status": "awaiting evidence" if name == "gate" else "ready",
            "provenance": f"audit:{name}",
            "summary": f"Canonical {name} evidence",
            "items": items if name == "findings" else [],
        }
        for name in HOST_DASHBOARD_COMPONENTS
    }
    return HostSurfaceSnapshot.create(
        workflow_id="wf", run_id="run", target="repo", revision="abc123",
        sequence=7, stage="review", state="awaiting_approval", values=values,
        evidence=("sha256:evidence",), safe_actions=actions,
    )


def _controller(markup):
    match = re.search(r"<script>(.*?)</script>", markup, re.DOTALL)
    assert match, "surface must emit its interaction controller"
    return match.group(1)


def _run_node(source):
    node = shutil.which("node")
    assert node, "Node.js is required to execute emitted dashboard JavaScript"
    completed = subprocess.run(
        [node, "-e", source], text=True, encoding="utf-8", errors="replace",
        capture_output=True, check=False)
    assert completed.returncode == 0, completed.stderr or completed.stdout


def _contrast(foreground, background):
    def luminance(color):
        channels = [int(color[offset:offset + 2], 16) / 255
                    for offset in (1, 3, 5)]
        converted = [
            channel / 12.92 if channel <= .04045
            else ((channel + .055) / 1.055) ** 2.4
            for channel in channels
        ]
        return .2126 * converted[0] + .7152 * converted[1] + .0722 * converted[2]
    high, low = sorted(
        (luminance(foreground), luminance(background)), reverse=True)
    return (high + .05) / (low + .05)


def test_h01_tabs_follow_aria_keyboard_model():
    markup = _widget_tabs("color:inherit")
    assert 'role="tablist"' in markup
    assert 'aria-controls="tp-panel-loop" tabindex="0" aria-selected="true"' in markup
    assert 'aria-controls="tp-panel-map" tabindex="-1" aria-selected="false"' in markup
    assert "tpTabKey(event,'loop')" in markup
    assert "tpTabKey(event,'map')" in markup
    panels = _widget_detail_panels({
        "pipe_d": "pipeline", "journey_d": "journey",
        "loop_panel": "execution", "map_panel": "context",
    })
    assert 'id="tp-panel-loop" role="tabpanel" aria-labelledby="tp-tab-loop"' in panels
    assert 'id="tp-panel-map" role="tabpanel" aria-labelledby="tp-tab-map" hidden' in panels
    controller = _WIDGET_JS.removeprefix("<script>").removesuffix("</script>")
    for key in ("ArrowLeft", "ArrowRight", "Home", "End"):
        assert json.dumps(key) in controller
    assert 'p.hidden=!on' in controller
    assert 'b.setAttribute("tabindex",on?"0":"-1")' in controller
    assert 'if(on&&moveFocus)b.focus()' in controller
    harness = r'''
class Target {
  constructor(){this.dataset={};this.style={};this.attrs={};this.hidden=false;}
  addEventListener(){} setAttribute(k,v){this.attrs[k]=v;}
  removeAttribute(k){delete this.attrs[k];} focus(){document.activeElement=this;}
}
const elements={}; ["tp-simple","tp-detail","tp-vb-simple","tp-vb-detail",
  "tp-detail-tabs","tp-panel-loop","tp-panel-map","tp-tab-loop","tp-tab-map"].forEach(
  function(id){elements[id]=new Target();});
const reviewRoot={dataset:{},addEventListener(){},contains(){return true;}};
global.tpFilter=function(){};
global.document={activeElement:null,createElement(){return new Target();},
  getElementById(id){return id==="tp-inline-review-root"?reviewRoot:(elements[id]||null);},
  getElementsByClassName(){return [];}};
global.window={};
''' + controller + r'''
let prevented=false;
tpTabKey({key:"ArrowRight",preventDefault(){prevented=true;}},"loop");
if(!prevented || document.activeElement!==elements["tp-tab-map"]) process.exit(2);
if(elements["tp-tab-loop"].attrs["aria-selected"]!=="false" ||
   elements["tp-tab-loop"].attrs.tabindex!=="-1") process.exit(3);
if(elements["tp-tab-map"].attrs["aria-selected"]!=="true" ||
   elements["tp-tab-map"].attrs.tabindex!=="0") process.exit(4);
if(!elements["tp-panel-loop"].hidden || elements["tp-panel-map"].hidden) process.exit(5);
tpTabKey({key:"Home",preventDefault(){}},"map");
if(document.activeElement!==elements["tp-tab-loop"]) process.exit(6);
'''
    _run_node(harness)


def test_h16_fallback_action_is_functional_or_truthfully_absent():
    markup = render_native_dashboard_surface(
        native_dashboard_projection(_snapshot(), host="codex"))
    controller = _controller(markup)
    harness = r'''
class Target {
  constructor(text="") { this.textContent=text; this.dataset={}; this.listeners={};
    this.attributes={}; this.disabled=false; this.value=""; }
  addEventListener(name, fn) { this.listeners[name]=fn; }
  emit(name, event={}) { return this.listeners[name](event); }
  setAttribute(name, value) { this.attributes[name]=value; }
  removeAttribute(name) { delete this.attributes[name]; }
}
const action=new Target("approve"); action.dataset.prompt="approve";
const status=new Target();
const root={querySelector(selector){
  if(selector==="[data-delivery-status]") return status;
  return null;
},querySelectorAll(selector){return selector==="[data-dashboard-action]"?[action]:[];}};
global.document={currentScript:{closest(){return root;}}};
global.window={};
''' + controller + r'''
action.emit("click");
if(action.disabled || action.textContent!=="approve") process.exit(2);
if(status.textContent!=="No chat bridge in this static view — reply in chat: approve") process.exit(3);
'''
    _run_node(harness)


def test_h17_fallback_retains_required_workflow_evidence():
    projection = native_dashboard_projection(_snapshot(), host="codex")
    markup = render_native_dashboard_surface(projection)
    assert "Canonical findings evidence" in markup
    assert "sha256:evidence" in markup
    assert "abc123" in markup and "awaiting_approval" in markup
    for index in range(9):
        assert f'data-item-id="F-{index:02d}"' in markup
        assert f"Finding {index}" in markup
    assert 'data-carousel-page="1"' in markup
    assert 'data-carousel-page="2" hidden' in markup
    assert 'data-carousel-direction="previous" disabled' in markup
    assert 'data-carousel-direction="next"' in markup
    assert 'data-carousel-filters="{}"' in markup
    controller = _controller(markup)
    assert 'carousel.dataset.carouselCurrent=String(next)' in controller
    assert 'page.hidden=Number(page.dataset.carouselPage)!==next' in controller


def test_h17_workflow_evidence_details_is_reachable_with_zero_to_two_actions():
    """Evidence never loses its control merely because action count is low."""
    for actions in ((), ("inspect",), ("inspect", "export")):
        markup = render_native_dashboard_surface(
            native_dashboard_projection(
                _snapshot(actions=actions), host="codex"))
        assert markup.count('data-detail-trigger="true"') == 1
        assert 'aria-controls="tp-fullscreen-detail"' in markup
        assert '<h3 id="tp-evidence-title">Workflow evidence</h3>' in markup
        nav = re.search(
            r'<nav class="tp-actions"[\s\S]*?</nav>', markup).group(0)
        assert nav.count("<button") <= 2
        if len(actions) == 2:
            dialog = re.search(
                r'<dialog id="tp-fullscreen-detail"[\s\S]*?</dialog>',
                markup).group(0)
            assert 'data-prompt="export"' in dialog


def test_h18_gate_action_waits_for_bridge_confirmation():
    controller = _WIDGET_JS.removeprefix("<script>").removesuffix("</script>")
    harness = r'''
(async function(){
class Target {
  constructor(label="") { this.innerHTML=label; this.textContent=label; this.dataset={};
    this.style={}; this.attrs={}; this.disabled=false; this.children=[]; }
  setAttribute(k,v){this.attrs[k]=v;} removeAttribute(k){delete this.attrs[k];}
  addEventListener(){} focus(){global.document.activeElement=this;}
}
const first=new Target("approve plan"), second=new Target("request changes");
const parent={status:null,querySelector(s){return s==="[data-tp-delivery-status]"?this.status:null;},
  querySelectorAll(){return [first,second];},appendChild(x){this.status=x;x.parentNode=this;}};
first.parentNode=parent; second.parentNode=parent;
const elements={}; ["tp-simple","tp-detail","tp-vb-simple","tp-vb-detail",
  "tp-panel-loop","tp-panel-map","tp-tab-loop","tp-tab-map"].forEach(
  function(id){elements[id]=new Target();});
const reviewRoot={dataset:{},addEventListener(){},contains(){return true;}};
global.tpFilter=function(){};
global.document={activeElement:null,createElement(){return new Target();},
  getElementById(id){return id==="tp-inline-review-root"?reviewRoot:(elements[id]||null);},
  getElementsByClassName(){return [];}};
let rejectSend;
global.window={openai:{sendFollowUpMessage(){return new Promise(function(resolve,reject){rejectSend=reject;});}}};
''' + controller + r'''
tpFire(first,"approve the plan","approved");
if(first.innerHTML!=="sending…" || !first.disabled || !second.disabled) process.exit(2);
if(first.innerHTML.includes("approved")) process.exit(3);
rejectSend(new Error("offline")); await new Promise(function(resolve){setImmediate(resolve);});
if(first.disabled || second.disabled || first.innerHTML!=="approve plan") process.exit(4);
if(!parent.status || !parent.status.textContent.includes("delivery failed")) process.exit(5);
if(!parent.status.textContent.includes("reply in chat: approve the plan")) process.exit(6);
let resolveSend;
window.openai.sendFollowUpMessage=function(){return new Promise(function(resolve){resolveSend=resolve;});};
tpFire(first,"approve the plan","approved");
if(first.innerHTML!=="sending…" || first.innerHTML.includes("approved")) process.exit(7);
resolveSend({delivered:false}); await new Promise(function(resolve){setImmediate(resolve);});
if(first.disabled || second.disabled || first.innerHTML!=="approve plan") process.exit(8);
if(!parent.status.textContent.includes("delivery failed") ||
   parent.status.textContent.includes("delivered to chat")) process.exit(9);
window.openai.sendFollowUpMessage=function(){return Promise.resolve({delivered:true});};
tpFire(first,"approve the plan","approved");
await new Promise(function(resolve){setImmediate(resolve);});
if(!first.innerHTML.includes("approved") || parent.status.textContent!=="delivered to chat") process.exit(10);
})();
'''
    _run_node(harness)


def test_h20_host_native_actions_render_only_with_behavior():
    markup = render_native_dashboard_surface(
        native_dashboard_projection(_snapshot(), host="codex"))
    assert 'data-dashboard-action="true"' in markup
    assert 'form.addEventListener("submit"' in markup
    assert 'event.preventDefault()' in markup
    assert "No chat bridge in this static view" in markup
    controller = _controller(markup)
    harness = r'''
class Target {
  constructor(){this.dataset={};this.listeners={};this.attributes={};this.disabled=false;
    this.textContent="";this.value="";}
  addEventListener(name,fn){this.listeners[name]=fn;}
  emit(name,event={}){return this.listeners[name](event);}
  setAttribute(name,value){this.attributes[name]=value;}
  removeAttribute(name){delete this.attributes[name];}
}
const input=new Target(); input.value="explain the gate";
const submit=new Target(); submit.textContent="Send";
const form=new Target(); form.querySelector=function(selector){
  return selector==="textarea"?input:submit;};
const status=new Target();
const root={querySelector(selector){
  if(selector==="[data-delivery-status]") return status;
  if(selector===".tp-composer") return form;
  return null;
},querySelectorAll(){return [];}};
global.document={currentScript:{closest(){return root;}}}; global.window={};
''' + controller + r'''
let prevented=false;form.emit("submit",{preventDefault(){prevented=true;}});
if(!prevented || submit.disabled || submit.textContent!=="Send") process.exit(2);
if(status.textContent!=="No chat bridge in this static view — reply in chat: explain the gate") process.exit(3);
'''
    _run_node(harness)


def test_h21_async_gate_exposes_pending_success_and_failure():
    markup = render_native_dashboard_surface(
        native_dashboard_projection(_snapshot(), host="codex"))
    controller = _controller(markup)
    for state in (
        'control.textContent="Sending…"',
        'control.textContent="Sent"',
        'report(control,"Delivery failed — retry or reply in chat: "+message,true)',
    ):
        assert state in controller
    assert 'return Promise.resolve(result)' in controller
    assert 'item.disabled=false' in controller
    assert 'control.removeAttribute("aria-busy")' in controller
    harness = r'''
(async function(){
class Target {
  constructor(text="") { this.textContent=text; this.dataset={}; this.listeners={};
    this.attributes={}; this.disabled=false; }
  addEventListener(name,fn){this.listeners[name]=fn;}
  emit(name,event={}){return this.listeners[name](event);}
  setAttribute(name,value){this.attributes[name]=value;}
  removeAttribute(name){delete this.attributes[name];}
}
const action=new Target("approve"); action.dataset.prompt="approve";
const status=new Target();
const root={querySelector(selector){return selector==="[data-delivery-status]"?status:null;},
  querySelectorAll(selector){return selector==="[data-dashboard-action]"?[action]:[];}};
global.document={currentScript:{closest(){return root;}}};
let rejectSend;
global.window={openai:{sendFollowUpMessage(){return new Promise(function(resolve,reject){rejectSend=reject;});}}};
''' + controller + r'''
action.emit("click");
if(action.textContent!=="Sending…" || !action.disabled || action.attributes["aria-busy"]!=="true") process.exit(2);
rejectSend(new Error("offline")); await new Promise(function(resolve){setImmediate(resolve);});
if(action.textContent!=="approve" || action.disabled || status.attributes.role!=="alert") process.exit(3);
let resolveSend;
window.openai.sendFollowUpMessage=function(){return new Promise(function(resolve){resolveSend=resolve;});};
action.emit("click");
if(action.textContent==="Sent") process.exit(4);
resolveSend(); await new Promise(function(resolve){setImmediate(resolve);});
if(action.textContent!=="Sent" || status.textContent!=="Delivered to chat: approve") process.exit(5);
})();
'''
    _run_node(harness)


def test_h21_detail_action_reports_inside_active_modal_and_retries_truthfully():
    """Detail actions keep every delivery state visible in the modal."""
    markup = render_native_dashboard_surface(
        native_dashboard_projection(_snapshot(), host="codex"))
    assert re.search(
        r'<dialog[^>]+id="tp-fullscreen-detail"[\s\S]*?'
        r'data-delivery-scope="detail"[^>]+role="status"[^>]+'
        r'aria-live="polite"[^>]+aria-atomic="true"', markup)
    controller = _controller(markup)
    assert re.search(
        r'data-action-classification="mutually-exclusive"[^>]+data-prompt="approve"',
        markup)
    assert re.search(
        r'data-action-classification="independent"[^>]+data-prompt="inspect"',
        markup)
    assert re.search(
        r'data-action-classification="independent"[^>]+data-prompt="export"',
        markup)
    harness = r'''
(async function(){
class Target {
  constructor(text="") { this.textContent=text; this.dataset={}; this.listeners={};
    this.attributes={}; this.disabled=false; this.open=false; this.surface=null;
    this.group=null; }
  addEventListener(name,fn){this.listeners[name]=fn;}
  emit(name,event={}){return this.listeners[name](event);}
  setAttribute(name,value){this.attributes[name]=value;if(name==="open")this.open=true;}
  removeAttribute(name){delete this.attributes[name];if(name==="open")this.open=false;}
  focus(){document.activeElement=this;}
  closest(selector){if(selector===".tp-detail")return this.surface;
    if(selector==="[data-delivery-actions]")return this.group;return null;}
}
const trigger=new Target("Details"), closer=new Target("Close");
const localStatus=new Target(), sharedStatus=new Target();
const inspect=new Target("inspect"), exportAction=new Target("export");
inspect.dataset.prompt="inspect";exportAction.dataset.prompt="export";
const detailGroup={querySelectorAll(selector){
  return selector==="[data-dashboard-action]"?[inspect,exportAction]:[];}};
const dialog=new Target();dialog.querySelector=function(selector){
  return selector==="[data-delivery-scope=detail]"?localStatus:null;};
dialog.showModal=function(){this.open=true;};dialog.close=function(){this.open=false;};
inspect.surface=dialog;inspect.group=detailGroup;
exportAction.surface=dialog;exportAction.group=detailGroup;
const root={querySelector(selector){
  if(selector==="[data-delivery-scope=shared]" || selector==="[data-delivery-status]")
    return sharedStatus;
  if(selector==="[data-detail-trigger]")return trigger;
  if(selector==="#tp-fullscreen-detail")return dialog;
  if(selector==="[data-detail-close]")return closer;
  return null;
},querySelectorAll(selector){
  return selector==="[data-dashboard-action]"?[inspect,exportAction]:[];}};
global.document={activeElement:trigger,currentScript:{closest(){return root;}}};
global.window={};
''' + controller + r'''
trigger.emit("click");
if(!dialog.open)process.exit(2);
inspect.emit("click");
if(inspect.disabled || inspect.textContent!=="inspect")process.exit(3);
if(localStatus.textContent!==
   "No chat bridge in this static view — reply in chat: inspect")process.exit(4);
if(sharedStatus.textContent)process.exit(5);

window.openai={sendFollowUpMessage(){return Promise.resolve({delivered:false});}};
inspect.emit("click");
if(!inspect.disabled || !exportAction.disabled || inspect.textContent!=="Sending…")
  process.exit(6);
if(localStatus.textContent!=="Sending to chat…" ||
   localStatus.attributes.role!=="status")process.exit(7);
if(localStatus.textContent.includes("Delivered"))process.exit(8);
await new Promise(function(resolve){setImmediate(resolve);});
if(inspect.disabled || exportAction.disabled || inspect.textContent!=="inspect" ||
   exportAction.textContent!=="export")process.exit(9);
if(localStatus.attributes.role!=="alert" || localStatus.textContent!==
   "Delivery failed — retry or reply in chat: inspect")process.exit(10);
if(sharedStatus.textContent)process.exit(11);

let resolveRetry;
window.openai.sendFollowUpMessage=function(){return new Promise(function(resolve){
  resolveRetry=resolve;});};
inspect.emit("click");
if(inspect.textContent!=="Sending…" || !inspect.disabled || !exportAction.disabled)
  process.exit(12);
if(localStatus.textContent.includes("Delivered"))process.exit(13);
resolveRetry({delivered:true});
await new Promise(function(resolve){setImmediate(resolve);});
if(inspect.textContent!=="Sent" || localStatus.textContent!==
   "Delivered to chat: inspect" || localStatus.attributes.role!=="status")
  process.exit(14);
if(sharedStatus.textContent)process.exit(15);
if(exportAction.disabled || exportAction.textContent!=="export")process.exit(16);
if(!inspect.disabled)process.exit(17);
})();
'''
    _run_node(harness)


def test_l01_small_metadata_contrast_is_at_least_4_5_to_1():
    pair = re.search(
        r'background:(#[0-9a-f]{6}).*?'
        r'<span style="color:(#[0-9a-f]{6});font-weight:400;font-size:12px">'
        r'· step', _TEMPLATE, re.DOTALL)
    assert pair
    background, foreground = pair.groups()
    assert _contrast(foreground, background) >= 4.5
