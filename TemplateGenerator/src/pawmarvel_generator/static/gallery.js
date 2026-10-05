"use strict";

const state={concepts:[],votes:{},dirty:new Set(),saving:false};
const gallery=document.querySelector("#gallery");
const template=document.querySelector("#card-template");
const nameInput=document.querySelector("#reviewer-name");
const idInput=document.querySelector("#reviewer-id");
const collectionFilter=document.querySelector("#collection-filter");
const titleFilter=document.querySelector("#title-filter");
const bottomLineFilter=document.querySelector("#bottom-line-filter");
const voteFilter=document.querySelector("#vote-filter");
const selectionFilter=document.querySelector("#selection-filter");
const progress=document.querySelector("#progress");
const saveAllButton=document.querySelector("#save-all");
const saveStatus=document.querySelector("#save-status");
const reviewDialog=document.querySelector("#review-dialog");
const reviewTitle=document.querySelector("#review-title");
const reviewContent=document.querySelector("#review-content");

nameInput.value=localStorage.getItem("pawmarvelReviewerName")||"";
idInput.value=localStorage.getItem("pawmarvelReviewerId")||"";

function identity(required=true){
  const reviewer_name=nameInput.value.trim();
  const reviewer_id=idInput.value.trim();
  if(required&&(!reviewer_name||!reviewer_id))throw new Error("Enter your name and email/team ID first.");
  if(reviewer_name)localStorage.setItem("pawmarvelReviewerName",reviewer_name);
  if(reviewer_id)localStorage.setItem("pawmarvelReviewerId",reviewer_id);
  return {reviewer_name,reviewer_id};
}

async function request(url,options={}){
  const response=await fetch(url,options);
  const payload=await response.json();
  if(!response.ok)throw new Error(payload.error||`Request failed (${response.status})`);
  return payload;
}

function cardVote(card){
  const selected=card.querySelector("input:checked");
  if(!selected)return null;
  return {
    design_id:card.dataset.designId,
    choice:selected.value,
    comment:card.querySelector("textarea").value.trim()
  };
}

function voteMatches(left,right){
  return Boolean(left&&right&&left.choice===right.choice&&(left.comment||"")===(right.comment||""));
}

function updateSaveControls(preserveStatus=false){
  const count=state.dirty.size;
  saveAllButton.disabled=state.saving||count===0;
  saveAllButton.textContent=count?`Save all changes (${count})`:"All changes saved";
  if(!preserveStatus&&!state.saving&&count){
    saveStatus.textContent=`${count} unsaved ${count===1?"change":"changes"}`;
    saveStatus.className="unsaved";
  }else if(!preserveStatus&&!state.saving&&!count){
    saveStatus.textContent=Object.keys(state.votes).length?"Saved":"No changes yet";
    saveStatus.className=Object.keys(state.votes).length?"saved":"";
  }
}

function updateProgress(){
  const visible=[...gallery.querySelectorAll(".card:not(.hidden)")];
  const voted=visible.filter(card=>card.querySelector("input:checked")).length;
  progress.textContent=`${voted} of ${visible.length} visible designs selected · ${state.dirty.size} unsaved`;
  updateSaveControls();
}

function updateCardState(card){
  const designId=card.dataset.designId;
  const current=cardVote(card);
  const prior=state.votes[designId];
  const hasDraft=Boolean(current||card.querySelector("textarea").value.trim());
  const dirty=hasDraft&&!voteMatches(current,prior);
  const status=card.querySelector(".status");
  state.dirty[dirty?"add":"delete"](designId);
  card.classList.toggle("dirty",dirty);
  card.classList.remove("invalid");
  status.className="status";
  if(dirty){status.textContent="Unsaved";status.classList.add("unsaved")}
  else if(prior){status.textContent="Saved";status.classList.add("saved")}
  else status.textContent="";
  applyFilters();
}

function applyFilters(){
  const collection=collectionFilter.value;
  const title=titleFilter.value.trim().toLocaleLowerCase();
  const bottomLine=bottomLineFilter.value.trim().toLocaleLowerCase();
  const vote=voteFilter.value;
  const selection=selectionFilter.value;
  gallery.querySelectorAll(".card").forEach(card=>{
    const current=cardVote(card);
    const matchCollection=!collection||card.dataset.collection===collection;
    const matchTitle=!title||card.dataset.title.includes(title);
    const matchBottomLine=!bottomLine||card.dataset.bottomLine.includes(bottomLine);
    const matchVote=!vote||current?.choice===vote;
    const matchSelection=!selection||(selection==="selected"?Boolean(current):!current);
    card.classList.toggle("hidden",!(matchCollection&&matchTitle&&matchBottomLine&&matchVote&&matchSelection));
  });
  updateProgress();
}

function clearFilters(){
  collectionFilter.value="";
  titleFilter.value="";
  bottomLineFilter.value="";
  voteFilter.value="";
  selectionFilter.value="";
  applyFilters();
}

function populateCollections(){
  const current=collectionFilter.value;
  const collections=[...new Set(state.concepts.map(item=>item.collection).filter(Boolean))].sort((left,right)=>left.localeCompare(right));
  collectionFilter.replaceChildren(new Option("All collections",""),...collections.map(value=>new Option(value,value)));
  if(collections.includes(current))collectionFilter.value=current;
}

function render(){
  state.dirty.clear();
  gallery.replaceChildren();
  for(const concept of state.concepts){
    const card=template.content.firstElementChild.cloneNode(true);
    card.dataset.designId=concept.design_id;
    card.dataset.collection=concept.collection;
    card.dataset.title=[concept.design_id,concept.headline].join(" ").toLocaleLowerCase();
    card.dataset.bottomLine=String(concept.bottom_line||"").toLocaleLowerCase();
    const image=card.querySelector("img"); image.src=concept.image_url; image.alt=`${concept.headline} — ${concept.bottom_line}`;
    card.querySelector(".meta").textContent=`Priority ${concept.priority} · ${concept.collection} · ${concept.concept} · option ${concept.bottom_line_option}`;
    card.querySelector("h2").textContent=concept.headline;
    card.querySelector(".slogan").textContent=concept.bottom_line;
    card.querySelector("code").textContent=concept.design_id;
    const reviewButton=card.querySelector(".review-context");
    if(concept.review_contexts.length){
      reviewButton.textContent="View generated review";
      reviewButton.disabled=false;
      reviewButton.addEventListener("click",()=>openReview(concept));
    }else{
      reviewButton.disabled=true;
      reviewButton.title="A complete art comparison and release-pet comparison are not available for the same product profile.";
    }
    card.querySelectorAll("input[type=radio]").forEach(radio=>{
      radio.name=`vote-${concept.design_id}`;
      radio.addEventListener("change",()=>updateCardState(card));
    });
    card.querySelector("textarea").addEventListener("input",()=>updateCardState(card));
    const prior=state.votes[concept.design_id];
    if(prior){
      const selected=card.querySelector(`input[value="${prior.choice}"]`); if(selected)selected.checked=true;
      card.querySelector("textarea").value=prior.comment||"";
      card.querySelector(".status").textContent="Saved"; card.querySelector(".status").classList.add("saved");
    }
    gallery.append(card);
  }
  applyFilters();
}

function element(tag,text,className=""){
  const node=document.createElement(tag);
  if(text!==undefined)node.textContent=text;
  if(className)node.className=className;
  return node;
}

function reviewPanel(title,imageUrl,evaluationUrl,reviewId){
  const panel=element("div",undefined,"review-panel");
  const heading=element("p");
  heading.append(element("strong",title),element("span",reviewId));
  const link=document.createElement("a");
  link.href=imageUrl;link.target="_blank";link.rel="noopener";
  const image=document.createElement("img");
  image.src=imageUrl;image.alt=`${title} contact sheet`;image.loading="lazy";
  link.append(image);
  const evaluation=document.createElement("a");
  evaluation.href=evaluationUrl;evaluation.target="_blank";evaluation.rel="noopener";
  evaluation.textContent="Open evaluation JSON";
  panel.append(heading,link,evaluation);
  return panel;
}

function openReview(concept){
  reviewTitle.textContent=concept.design_id;
  reviewContent.replaceChildren();
  for(const context of concept.review_contexts){
    const section=element("section",undefined,"review-profile");
    section.append(element("h3",context.product_profile_id));
    const grid=element("div",undefined,"review-grid");
    grid.append(
      reviewPanel("Art-template comparison",context.art_comparison_url,context.art_evaluation_url,context.art_review_id),
      reviewPanel("Release pet comparison",context.pet_comparison_url,context.pet_evaluation_url,context.pet_review_id)
    );
    section.append(grid);
    reviewContent.append(section);
  }
  reviewDialog.showModal();
}

async function loadVotes(){
  if(state.dirty.size&&!confirm("Discard unsaved changes and reload your saved votes?"))return;
  try{
    const who=identity(true);
    const data=await request(`/api/gallery?reviewer=${encodeURIComponent(who.reviewer_id)}`);
    state.concepts=data.concepts; state.votes=data.current_votes||{}; populateCollections(); render();
    saveStatus.textContent="Saved votes loaded"; saveStatus.className="saved";
  }catch(error){saveStatus.textContent=error.message;saveStatus.className="error"}
}

function invalidDirtyCards(){
  const invalid=[];
  for(const designId of state.dirty){
    const card=gallery.querySelector(`[data-design-id="${CSS.escape(designId)}"]`);
    if(card&&!cardVote(card))invalid.push(card);
  }
  return invalid;
}

async function saveAllVotes(){
  if(!state.dirty.size)return;
  let who;
  try{who=identity(true)}
  catch(error){
    saveStatus.textContent=error.message;saveStatus.className="error";
    (nameInput.value.trim()?idInput:nameInput).focus();
    return;
  }
  const invalid=invalidDirtyCards();
  if(invalid.length){
    clearFilters();
    for(const card of invalid){
      card.classList.add("invalid");
      const cardStatus=card.querySelector(".status");
      cardStatus.textContent="Choose a recommendation before saving";cardStatus.className="status error";
    }
    invalid[0].scrollIntoView({behavior:"smooth",block:"center"});
    invalid[0].querySelector("input").focus();
    saveStatus.textContent=`${invalid.length} changed ${invalid.length===1?"design needs":"designs need"} a recommendation`;
    saveStatus.className="error";
    return;
  }
  const cards=[...state.dirty].map(designId=>gallery.querySelector(`[data-design-id="${CSS.escape(designId)}"]`));
  const votes=cards.map(cardVote);
  state.saving=true;updateSaveControls();
  saveAllButton.textContent=`Saving ${votes.length}…`;
  saveStatus.textContent="Saving all changed reviews…";saveStatus.className="";
  try{
    const data=await request("/api/votes",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({...who,votes})});
    for(const vote of data.votes)state.votes[vote.design_id]=vote;
    for(const card of cards)updateCardState(card);
    saveStatus.textContent=`Saved ${data.saved_count} ${data.saved_count===1?"change":"changes"}`;
    saveStatus.className="saved";
  }catch(error){
    saveStatus.textContent=`Nothing was saved: ${error.message}`;saveStatus.className="error";
  }finally{state.saving=false;updateSaveControls(true)}
}

document.querySelector("#load-votes").addEventListener("click",loadVotes);
saveAllButton.addEventListener("click",saveAllVotes);
document.querySelector("#close-review").addEventListener("click",()=>reviewDialog.close());
reviewDialog.addEventListener("click",event=>{if(event.target===reviewDialog)reviewDialog.close()});
for(const filter of [collectionFilter,titleFilter,bottomLineFilter,voteFilter,selectionFilter])filter.addEventListener(filter.matches("input[type=search]")?"input":"change",applyFilters);
document.querySelector("#clear-filters").addEventListener("click",clearFilters);
window.addEventListener("beforeunload",event=>{if(state.dirty.size){event.preventDefault();event.returnValue=""}});
document.addEventListener("keydown",event=>{if((event.metaKey||event.ctrlKey)&&event.key.toLowerCase()==="s"){event.preventDefault();saveAllVotes()}});

(async()=>{
  try{
    const reviewer=idInput.value.trim();
    const data=await request(`/api/gallery${reviewer?`?reviewer=${encodeURIComponent(reviewer)}`:""}`);
    state.concepts=data.concepts; state.votes=data.current_votes||{};
    populateCollections();
    render();
  }catch(error){progress.textContent=error.message;progress.classList.add("error")}
})();
