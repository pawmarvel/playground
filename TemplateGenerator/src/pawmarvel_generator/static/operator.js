"use strict";

const state={dashboard:null,tab:"active",selected:new Set(),pending:false};
const root=document.querySelector("#designs");
const template=document.querySelector("#design-template");
const status=document.querySelector("#status");
const operatorInput=document.querySelector("#operator-id");
const batchToolbar=document.querySelector("#batch-toolbar");
const selectedCount=document.querySelector("#selected-count");
const graduateSelected=document.querySelector("#graduate-selected");
const abandonSelected=document.querySelector("#abandon-selected");
const restoreSelected=document.querySelector("#restore-selected");
const collectionFilter=document.querySelector("#collection-filter");
const titleFilter=document.querySelector("#title-filter");
const bottomLineFilter=document.querySelector("#bottom-line-filter");
const voteFilter=document.querySelector("#vote-filter");
const selectionFilter=document.querySelector("#selection-filter");
const filterCount=document.querySelector("#filter-count");
operatorInput.value=localStorage.getItem("pawmarvelOperatorId")||"";

async function request(url,options={}){
  const response=await fetch(url,options); const payload=await response.json();
  if(!response.ok)throw new Error(payload.error||`Request failed (${response.status})`);
  return payload;
}

function node(tag,text,className=""){
  const value=document.createElement(tag); if(text!==undefined)value.textContent=text;
  if(className)value.className=className; return value;
}

function choiceLabel(choice){
  return {graduate:"Graduate",consider:"Improve",pass:"Abandon"}[choice]||choice;
}

function actionButton(label,action,className=""){
  const button=node("button",label,className);button.type="button";
  button.addEventListener("click",event=>runAction(event.currentTarget.dataset.designId,action));
  return button;
}

function matchesText(value,query){return String(value||"").toLocaleLowerCase().includes(query)}

function filteredDesigns(){
  const designs=state.dashboard?.[state.tab]||[];
  const collection=collectionFilter.value;
  const title=titleFilter.value.trim().toLocaleLowerCase();
  const bottomLine=bottomLineFilter.value.trim().toLocaleLowerCase();
  const vote=voteFilter.value;
  const selection=selectionFilter.value;
  return designs.filter(design=>{
    if(collection&&design.collection!==collection)return false;
    if(title&&!matchesText(`${design.headline||""} ${design.design_id}`,title))return false;
    if(bottomLine&&!matchesText(design.bottom_line,bottomLine))return false;
    if(vote==="unvoted"&&design.vote_count!==0)return false;
    if(["graduate","consider","pass"].includes(vote)&&design.vote_totals[vote]===0)return false;
    const selected=state.selected.has(design.design_id);
    if(selection==="selected"&&!selected)return false;
    if(selection==="unselected"&&selected)return false;
    return true;
  });
}

function populateCollections(){
  const current=collectionFilter.value;
  const designs=["active","abandoned","graduated"].flatMap(pool=>state.dashboard?.[pool]||[]);
  const collections=[...new Set(designs.map(design=>design.collection).filter(Boolean))].sort((left,right)=>left.localeCompare(right));
  collectionFilter.replaceChildren(new Option("All collections",""),...collections.map(value=>new Option(value,value)));
  if(collections.includes(current))collectionFilter.value=current;
}

function updateBatchToolbar(){
  const poolIds=new Set((state.dashboard?.[state.tab]||[]).map(design=>design.design_id));
  for(const designId of state.selected)if(!poolIds.has(designId))state.selected.delete(designId);
  batchToolbar.hidden=false;
  selectedCount.textContent=`${state.selected.size} selected`;
  const unavailable=state.pending||state.selected.size===0;
  graduateSelected.hidden=state.tab!=="active";
  abandonSelected.hidden=state.tab!=="active";
  restoreSelected.hidden=state.tab==="active";
  graduateSelected.disabled=unavailable;
  abandonSelected.disabled=unavailable;
  restoreSelected.disabled=unavailable;
  selectionFilter.disabled=false;
}

function render(){
  root.replaceChildren(); if(!state.dashboard)return;
  updateBatchToolbar();
  document.querySelectorAll(".tabs button").forEach(button=>{
    button.classList.toggle("selected",button.dataset.tab===state.tab);
    button.querySelector("span").textContent=`(${state.dashboard[button.dataset.tab].length})`;
  });
  const poolDesigns=state.dashboard[state.tab];
  const designs=filteredDesigns();
  filterCount.textContent=`Showing ${designs.length} of ${poolDesigns.length} designs in this pool`;
  if(!designs.length){root.append(node("p",poolDesigns.length?"No designs match the current filters.":"No designs in this pool.","empty"));return}
  for(const design of designs){
    const card=template.content.firstElementChild.cloneNode(true);
    const selector=card.querySelector(".batch-choice");
    const checkbox=selector.querySelector("input");
    checkbox.checked=state.selected.has(design.design_id);
    card.classList.toggle("selected",checkbox.checked);
    checkbox.addEventListener("change",()=>{
      if(checkbox.checked)state.selected.add(design.design_id);
      else state.selected.delete(design.design_id);
      if(selectionFilter.value)render();
      else{card.classList.toggle("selected",checkbox.checked);updateBatchToolbar()}
    });
    card.querySelector("img").src=design.operator_image_url;
    card.querySelector("img").alt=design.headline||design.design_id;
    card.querySelector(".meta").textContent=`${design.collection} · ${design.concept}`;
    card.querySelector("h2").textContent=design.headline||design.design_id;
    card.querySelector("code").textContent=design.design_id;
    card.querySelector(".slogan").textContent=design.bottom_line||"";
    card.querySelector(".rank").textContent=design.rank?`#${design.rank}`:"";
    const counts=card.querySelector(".counts");
    counts.append(
      node("span",`Graduate ${design.vote_totals.graduate}`,"count graduate"),
      node("span",`Improve ${design.vote_totals.consider}`,"count improve"),
      node("span",`Abandon ${design.vote_totals.pass}`,"count abandon"),
      node("span",`Total ${design.vote_count}`,"count")
    );
    card.querySelector(".round").textContent=`Review round ${design.review_round}${design.purge_after?` · feedback expires ${design.purge_after}`:""}`;
    const feedback=card.querySelector(".feedback");
    if(!design.feedback.length)feedback.append(node("p","No feedback in this round."));
    for(const vote of design.feedback){
      const item=node("article");
      item.append(node("strong",`${vote.reviewer_name} · ${choiceLabel(vote.choice)}`));
      item.append(node("p",vote.comment||"No written feedback.")); feedback.append(item);
    }
    const actions=card.querySelector(".actions");
    if(state.tab==="active"){
      actions.append(
        actionButton("Start new review round","new-round","secondary"),
        actionButton("Graduate","graduate"),
        actionButton("Abandon","abandon","danger")
      );
    }else actions.append(actionButton("Restore to active review","restore"));
    actions.querySelectorAll("button").forEach(button=>button.dataset.designId=design.design_id);
    root.append(card);
  }
}

async function load(){
  try{status.className="";status.textContent="Loading…";state.dashboard=await request("/api/operator/designs");populateCollections();render();status.textContent=`Updated ${new Date().toLocaleTimeString()}`}
  catch(error){status.textContent=error.message;status.className="error"}
}

async function runAction(designId,action){
  const operator_id=operatorInput.value.trim();
  if(!operator_id){alert("Enter your operator identity first.");operatorInput.focus();return}
  localStorage.setItem("pawmarvelOperatorId",operator_id);
  const labels={"new-round":"start a clean review round for","graduate":"graduate","abandon":"abandon","restore":"restore"};
  if(!confirm(`Confirm: ${labels[action]} ${designId}?`))return;
  const reason=prompt("Decision reason or improvement summary:",""); if(reason===null)return;
  try{
    status.className="";status.textContent="Applying decision…";
    const result=await request("/api/operator/action",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({action,design_id:designId,operator_id,reason})});
    state.dashboard=result.dashboard;render();status.textContent="Decision saved";
  }catch(error){status.textContent=error.message;status.className="error"}
}

async function runBatchAction(action){
  const operator_id=operatorInput.value.trim();
  if(!operator_id){alert("Enter your operator identity first.");operatorInput.focus();return}
  const design_ids=[...state.selected].sort();
  if(!design_ids.length)return;
  localStorage.setItem("pawmarvelOperatorId",operator_id);
  if(!confirm(`Confirm: ${action} ${design_ids.length} selected designs as one batch?\n\n${design_ids.join("\n")}`))return;
  const reason=prompt("Shared decision reason for this batch:",""); if(reason===null)return;
  try{
    state.pending=true;updateBatchToolbar();status.className="";status.textContent=`Applying ${action} batch…`;
    const result=await request("/api/operator/actions",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({action,design_ids,operator_id,reason})});
    const completed={graduate:"graduated",abandon:"abandoned",restore:"restored to active review"}[action];
    state.selected.clear();state.dashboard=result.dashboard;render();status.textContent=`${result.processed_count} designs ${completed}`;
  }catch(error){status.textContent=error.message;status.className="error"}
  finally{state.pending=false;updateBatchToolbar()}
}

document.querySelector("#refresh").addEventListener("click",load);
document.querySelectorAll(".tabs button").forEach(button=>button.addEventListener("click",()=>{state.tab=button.dataset.tab;render()}));
document.querySelector("#select-all").addEventListener("click",()=>{for(const design of filteredDesigns())state.selected.add(design.design_id);render()});
document.querySelector("#clear-selection").addEventListener("click",()=>{state.selected.clear();render()});
for(const filter of [collectionFilter,titleFilter,bottomLineFilter,voteFilter,selectionFilter])filter.addEventListener(filter.matches("input[type=search]")?"input":"change",render);
document.querySelector("#clear-filters").addEventListener("click",()=>{collectionFilter.value="";titleFilter.value="";bottomLineFilter.value="";voteFilter.value="";selectionFilter.value="";render()});
graduateSelected.addEventListener("click",()=>runBatchAction("graduate"));
abandonSelected.addEventListener("click",()=>runBatchAction("abandon"));
restoreSelected.addEventListener("click",()=>runBatchAction("restore"));
load();
