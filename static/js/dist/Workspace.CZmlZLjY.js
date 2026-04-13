import{r as c,j as e}from"./react-vendor.B9Kl00_S.js";import{e as Re,s as $e,w as se}from"./api.Cc-cQDtR.js";import{b as Ae,f as Fe,u as T,e as _e,g as Le,h as Me,i as Pe,j as ze,k as qe,l as De}from"./store.DQVBLgcG.js";import{t as i}from"./i18n.Dqvw2aaN.js";import{u as Ke,E as Qe,B as ae,C as He,m as Oe}from"./components.BYR6JQR5.js";import{c as k}from"./utils.CvEEV5tE.js";import{u as Ve,c as Be}from"./router.DRotNa0I.js";import"./zustand.BgdKfgGX.js";import"./hooks.D2WLQrUw.js";import"./query.C5KjnddJ.js";import"./charts.CNUZnH49.js";const ne=()=>`tab-${Date.now()}-${Math.random().toString(36).substr(2,9)}`,Xe=300*1e3,Ye=120*1e3,lt=()=>{const n=Ae(),$=Ke(),ie=Ve(),[g,K]=Be(),[h,xe]=c.useState(null),[f,he]=c.useState(null),[C,we]=c.useState(null),[Q,ke]=c.useState(!0),[H,ye]=c.useState(!0),[I,M]=c.useState("initializing"),[oe,re]=c.useState(null),[x,U]=c.useState([]),[S,A]=c.useState(""),[ve,O]=c.useState(new Set),[F,V]=c.useState(null),[W,P]=c.useState(""),[Ne,B]=c.useState(!1),[ce,X]=c.useState(!1),[je,Ie]=c.useState({}),[E,le]=c.useState(null),[z,Te]=c.useState(!1),v=Fe(),{toggleTabNotifications:de}=T(),_=c.useRef(new Map),N=_e(),{toggleWorkspaceFullscreen:me,exitWorkspaceFullscreen:ue}=T(),q=Le(),Y=Me(),R=Pe(),L=ze(),pe=qe(),be=De();c.useEffect(()=>{(async()=>{try{M("loadingConfig");const s=await se.getConfig();if(xe(s),s.enabled&&s.multi_user_mode){M("startingWorkspace");const a=await se.getUserWebUIUrl();a.success?(he(a),M("ready")):re(a.error||"Failed to get user workspace URL")}else M("ready")}catch(s){re(s?.message||"Failed to load workspace config")}finally{ke(!1)}})()},[]),c.useEffect(()=>{if(!h?.multi_user_mode||!f?.success)return;const s=setInterval(async()=>{try{await se.getUserWebUIUrl()}catch(a){console.error("Failed to send activity heartbeat:",a)}},Ye);return()=>clearInterval(s)},[h?.multi_user_mode,f?.success]),c.useEffect(()=>{const t=s=>{if(s.data?.type==="openace-enter-chat"&&T.getState().enterWorkspaceFullscreen(!1,!1),s.data?.type==="qwen-code-session-update"){const{sessionId:a,encodedProjectName:o,toolName:d,title:r,settings:u}=s.data;if(a){const p=T.getState().workspaceActiveTabId;if(p){const b={sessionId:a,encodedProjectName:o,toolName:d,settings:u};r&&(b.title=r),T.getState().updateWorkspaceTab(p,b),U(w=>w.map(m=>m.id===p?{...m,sessionId:a,encodedProjectName:o,toolName:d,title:r||m.title,settings:u}:m))}}}if(s.data?.type==="qwen-code-tab-notification"){const{isWaiting:a,waitingType:o}=s.data;if(v){let d=null;if(s.source){for(const[r,u]of _.current.entries())if(u.contentWindow===s.source){d=r;break}}d||(d=T.getState().workspaceActiveTabId),d&&(U(r=>r.map(u=>u.id===d?{...u,waitingForUser:a,waitingType:o}:u)),T.getState().updateWorkspaceTab(d,{waitingForUser:a,waitingType:o}))}}if(s.data?.type==="qwen-code-tab-switch-request"){const{direction:a}=s.data;U(o=>{if(o.length<=1)return o;const d=T.getState().workspaceActiveTabId,r=o.findIndex(b=>b.id===d);let u;a==="prev"?u=r<=0?o.length-1:r-1:u=r>=o.length-1?0:r+1;const p=o[u];return p&&p.id!==d&&(A(p.id),T.getState().setWorkspaceActiveTabId(p.id),setTimeout(()=>{const b=_.current.get(p.id);b?.contentWindow&&(b.contentWindow.postMessage({type:"openace-focus-input"},"*"),b.contentWindow.postMessage({type:"openace-tab-activated"},"*"))},100)),o})}};return window.addEventListener("message",t),()=>window.removeEventListener("message",t)},[v,n]);const D=c.useCallback(async()=>{try{const t=await Re.getQuotaStatus();we(t)}catch(t){console.error("Failed to check quota:",t)}finally{ye(!1)}},[]);c.useEffect(()=>{D();const t=setInterval(D,Xe);return()=>clearInterval(t)},[D]),c.useEffect(()=>{C?.over_quota?.any&&N&&(ue(),$.warning(i("exitedFullscreenDueToQuotaTitle",n),i("exitedFullscreenDueToQuotaDesc",n)))},[C?.over_quota?.any,N,ue,n,$]);const j=c.useCallback((t,s,a,o)=>{if(!h?.enabled)return"";const d=(p,b,w)=>{if(w===void 0)return p;const m=p.includes("?")?"&":"?";return`${p}${m}${b}=${encodeURIComponent(w)}`};if(h.multi_user_mode&&f?.success){const p=f.url,b=f.token,w=f.openace_url,m=p.includes("?")?"&":"?";let l=`${p}${m}token=${encodeURIComponent(b)}`;return w&&(l=`${l}&openace_url=${encodeURIComponent(w)}`),l=`${l}&lang=${encodeURIComponent(n)}`,t&&(l=`${l}&sessionId=${encodeURIComponent(t)}`),s&&(l=`${l}&encodedProjectName=${encodeURIComponent(s)}`),a&&(l=`${l}&toolName=${encodeURIComponent(a)}`),o?.model&&(l=`${l}&model=${encodeURIComponent(o.model)}`),o?.useWebUI!==void 0&&(l=`${l}&useWebUI=${o.useWebUI}`),o?.permissionMode&&(l=`${l}&permissionMode=${encodeURIComponent(o.permissionMode)}`),l}let r=h.url;const u=r.includes("?")?"&":"?";return r=`${r}${u}lang=${encodeURIComponent(n)}`,t&&(r=d(r,"sessionId",t)),s&&(r=d(r,"encodedProjectName",s)),a&&(r=d(r,"toolName",a)),o?.model&&(r=d(r,"model",o.model)),o?.useWebUI!==void 0&&(r=`${r}&useWebUI=${o.useWebUI}`),o?.permissionMode&&(r=d(r,"permissionMode",o.permissionMode)),r},[h,f,n]);c.useEffect(()=>{if(!h?.enabled||h.multi_user_mode&&!f?.success||z)return;const t=g.get("sessionId"),s=g.get("restoreSession"),a=g.get("encodedProjectName"),o=g.get("toolName"),d=g.get("model"),r=g.get("useWebUI"),u=g.get("permissionMode"),p=t||s;let b=[],w="";if(p){const m=d||r!==null||u?{model:d||void 0,useWebUI:r==="true"?!0:r==="false"?!1:void 0,permissionMode:u||void 0}:void 0,l=j(p,a||void 0,o||void 0,m);if(l){const y={id:ne(),title:i("restoredSession",n),url:l,token:f?.token||"",sessionId:p,encodedProjectName:a||void 0,toolName:o||void 0,settings:m,createdAt:Date.now(),waitingForUser:!1,waitingType:null};b=[y],w=y.id,L({id:y.id,title:y.title,sessionId:y.sessionId,encodedProjectName:y.encodedProjectName,toolName:y.toolName,settings:y.settings,createdAt:y.createdAt,waitingForUser:y.waitingForUser,waitingType:y.waitingType}),g.delete("sessionId"),g.delete("restoreSession"),g.delete("encodedProjectName"),g.delete("toolName"),K(g,{replace:!0})}}else if(q.length>0)b=q.map(m=>{const l=m.sessionId?j(m.sessionId,m.encodedProjectName,m.toolName,m.settings):j();return{...m,url:l||"",token:f?.token||""}}),w=q.find(m=>m.id===Y)?Y:b.length>0?b[0].id:"",console.log("[Issue #65] Restored workspace tabs from store:",{tabsCount:b.length,activeTabId:w});else{const m=j();if(m){const l={id:ne(),title:i("newSession",n),url:m,token:f?.token||"",createdAt:Date.now(),waitingForUser:!1,waitingType:null};b=[l],w=l.id,L({id:l.id,title:l.title,sessionId:l.sessionId,encodedProjectName:l.encodedProjectName,toolName:l.toolName,createdAt:l.createdAt,waitingForUser:l.waitingForUser,waitingType:l.waitingType})}}b.length>0&&(U(b),A(w),R(w),O(new Set(b.map(m=>m.id))),Te(!0))},[h,f,z,q,Y,n,j,g,K,L,R]),c.useEffect(()=>{const t=g.get("newTab"),s=j();t==="true"&&h?.enabled&&s&&z&&(g.delete("newTab"),K(g,{replace:!0}),fe())},[g,h,j,z]);const fe=c.useCallback(t=>{const s=j(t||void 0);if(!s)return;const a={id:ne(),title:t?i("restoredSession",n):i("newSession",n),url:s,token:f?.token||"",createdAt:Date.now(),waitingForUser:!1,waitingType:null};U(o=>[...o,a]),A(a.id),L({id:a.id,title:a.title,sessionId:a.sessionId,encodedProjectName:a.encodedProjectName,toolName:a.toolName,createdAt:a.createdAt,waitingForUser:a.waitingForUser,waitingType:a.waitingType}),R(a.id),O(o=>new Set(o).add(a.id))},[j,f,n,L,R]),Se=c.useCallback((t,s)=>{s.stopPropagation(),U(a=>{const o=a.filter(d=>d.id!==t);if(S===t&&o.length>0){const d=a.findIndex(u=>u.id===t),r=Math.min(d,o.length-1);A(o[r].id)}return o}),be(t)},[S,be]),G=c.useCallback(t=>{A(t),R(t),setTimeout(()=>{const s=_.current.get(t);s?.contentWindow&&(s.contentWindow.postMessage({type:"openace-focus-input"},"*"),s.contentWindow.postMessage({type:"openace-tab-activated"},"*"))},100)},[R]),Ce=c.useCallback((t,s)=>{s.stopPropagation();const a=x.find(o=>o.id===t);a&&(V(t),P(a.title),B(!0))},[x]),ge=c.useCallback(async()=>{if(!(!F||!W.trim())){X(!0);try{const t=x.find(a=>a.id===F);if(!t){X(!1);return}let s=t.sessionId||null;if(!s){const a=t.url.split("/c/");a.length>1&&(s=a[1].split("?")[0].split("#")[0])}if(s)try{const a=await $e.renameSession(s,W.trim());a.success||console.log("Session not found in backend, updating locally only:",a.error)}catch(a){console.log("Rename API failed, updating locally:",a)}U(a=>a.map(o=>o.id===F?{...o,title:W.trim()}:o)),pe(F,{title:W.trim()}),$.success(i("sessionRenamed",n)),B(!1),V(null),P("")}catch(t){console.error("Failed to rename session:",t),$.error(t.message||i("error",n))}finally{X(!1)}}},[F,W,x,n,$,pe]),J=c.useCallback(()=>{B(!1),V(null),P("")},[]),Ue=c.useCallback((t,s)=>{s.stopPropagation(),s.preventDefault(),le(t)},[]),Z=c.useCallback(t=>{if(!E)return;const s=document.querySelector(`[data-tab-id="${E}"]`);if(!s)return;const a=s.getBoundingClientRect(),o=t.clientX-a.left,d=Math.max(100,Math.min(400,o));Ie(r=>({...r,[E]:d}))},[E]),ee=c.useCallback(()=>{le(null)},[]);c.useEffect(()=>{if(E)return document.addEventListener("mousemove",Z),document.addEventListener("mouseup",ee),()=>{document.removeEventListener("mousemove",Z),document.removeEventListener("mouseup",ee)}},[E,Z,ee]);const We=c.useCallback(t=>{O(s=>{const a=new Set(s);return a.delete(t),a})},[]),Ee=c.useCallback(()=>{ie("/work/usage")},[ie]),te=C?.over_quota?.any??!1;if(c.useEffect(()=>{const t=s=>{if(te||Q||H||x.length<=1)return;const a=navigator.platform.toUpperCase().indexOf("MAC")>=0;if((a?s.metaKey&&s.shiftKey:s.ctrlKey&&s.shiftKey)&&(s.code==="Comma"||s.code==="Period")){s.preventDefault(),console.log("[Keyboard Shortcut] Detected:",{key:s.key,code:s.code,isMac:a,metaKey:s.metaKey,ctrlKey:s.ctrlKey,shiftKey:s.shiftKey,direction:s.code==="Comma"?"prev":"next",tabsLength:x.length,activeTabId:S});const d=x.findIndex(p=>p.id===S);let r;s.code==="Comma"?r=d<=0?x.length-1:d-1:r=d>=x.length-1?0:d+1;const u=x[r];u&&(console.log("[Keyboard Shortcut] Switching to tab:",u.id),G(u.id))}};return window.addEventListener("keydown",t),()=>{window.removeEventListener("keydown",t)}},[x.length,S,G,te,Q,H]),Q||H){const t=()=>{switch(I){case"loadingConfig":return i("loadingWorkspaceConfig",n)||"Loading workspace configuration...";case"startingWorkspace":return i("startingWorkspaceInstance",n)||"Starting your workspace instance...";case"ready":return i("workspaceReady",n)||"Workspace ready!";default:return i("loading",n)}},s=I==="startingWorkspace";return e.jsx("div",{className:"workspace-loading d-flex align-items-center justify-content-center h-100",children:e.jsxs("div",{className:"text-center",children:[e.jsx("div",{className:"spinner-border text-primary mb-3",role:"status",children:e.jsx("span",{className:"visually-hidden",children:i("loading",n)})}),e.jsx("h5",{className:"mb-2",children:t()}),s&&e.jsx("p",{className:"text-muted small mb-3",children:i("workspaceStartupNote",n)||"This may take a few seconds on first visit"}),e.jsxs("div",{className:"progress-steps mt-3",children:[e.jsxs("div",{className:`progress-step ${I==="loadingConfig"||I==="startingWorkspace"||I==="ready"?"active":""}`,children:[e.jsx("i",{className:"bi bi-check-circle-fill"}),e.jsx("span",{children:i("loadingConfig",n)||"Load config"})]}),e.jsxs("div",{className:`progress-step ${I==="startingWorkspace"||I==="ready"?"active":""}`,children:[e.jsx("i",{className:`bi ${I==="startingWorkspace"?"bi-arrow-repeat spin":"bi-check-circle-fill"}`}),e.jsx("span",{children:i("startingInstance",n)||"Start instance"})]}),e.jsxs("div",{className:`progress-step ${I==="ready"?"active":""}`,children:[e.jsx("i",{className:"bi bi-check-circle-fill"}),e.jsx("span",{children:i("ready",n)||"Ready"})]})]})]})})}return oe?e.jsx(Qe,{message:oe}):h?.enabled?h.multi_user_mode&&!f?.success?e.jsx("div",{className:"workspace",children:e.jsxs("div",{className:"text-center py-5",children:[e.jsx("i",{className:"bi bi-exclamation-circle fs-1 text-warning"}),e.jsx("h4",{className:"mt-3",children:i("workspaceUnavailable",n)}),e.jsx("p",{className:"text-muted",children:f?.error||i("workspaceUnavailableHelp",n)}),e.jsxs(ae,{variant:"primary",onClick:()=>window.location.reload(),children:[e.jsx("i",{className:"bi bi-arrow-clockwise me-2"}),i("retry",n)]})]})}):j()?te?e.jsxs("div",{className:"workspace h-100 d-flex flex-column",children:[e.jsx("div",{className:"page-header mb-3 px-3 pt-3",children:e.jsx("h2",{children:i("workspace",n)})}),e.jsx("div",{className:"flex-grow-1 d-flex align-items-center justify-content-center px-3",children:e.jsx(He,{className:"text-center",style:{maxWidth:"500px"},children:e.jsxs("div",{className:"py-4",children:[e.jsx("i",{className:"bi bi-exclamation-triangle-fill text-warning fs-1 mb-3"}),e.jsx("h4",{className:"text-danger mb-3",children:i("quotaExceeded",n)}),e.jsxs("p",{className:"text-muted mb-4",children:[C?.over_quota.daily_request&&e.jsx("span",{className:"d-block",children:i("dailyRequestQuotaExceeded",n)}),C?.over_quota.monthly_request&&e.jsx("span",{className:"d-block",children:i("monthlyRequestQuotaExceeded",n)}),C?.over_quota.daily_token&&e.jsx("span",{className:"d-block",children:i("dailyTokenQuotaExceeded",n)}),C?.over_quota.monthly_token&&e.jsx("span",{className:"d-block",children:i("monthlyTokenQuotaExceeded",n)})]}),e.jsx("p",{className:"text-muted small mb-4",children:i("quotaLimitsHelpDesc",n)}),e.jsxs("div",{className:"d-flex gap-2 justify-content-center",children:[e.jsxs(ae,{variant:"outline-primary",onClick:Ee,children:[e.jsx("i",{className:"bi bi-bar-chart me-2"}),i("myUsage",n)]}),e.jsxs(ae,{variant:"primary",onClick:D,children:[e.jsx("i",{className:"bi bi-arrow-clockwise me-2"}),i("retry",n)]})]})]})})})]}):e.jsxs("div",{className:k("workspace h-100 d-flex flex-column",N&&"fullscreen-mode"),children:[e.jsxs("div",{className:k("page-header mb-3 px-3 pt-3 d-flex align-items-center",N&&"d-none"),children:[e.jsxs("div",{className:"d-flex align-items-center flex-grow-1",children:[e.jsx("h2",{children:i("workspace",n)}),h.multi_user_mode&&f?.system_account&&e.jsxs("small",{className:"text-muted ms-2",children:["(",f.system_account,")"]})]}),e.jsxs("div",{className:"d-flex align-items-center gap-2",children:[e.jsxs("button",{className:k("btn btn-sm",v?"btn-outline-primary":"btn-outline-secondary"),onClick:de,title:v?i("disableTabNotifications",n)||"Disable tab notifications":i("enableTabNotifications",n)||"Enable tab notifications",children:[e.jsx("i",{className:k("bi me-1",v?"bi-bell-fill":"bi-bell-slash")}),e.jsx("span",{className:"d-none d-sm-inline",children:v?i("tabNotificationsOn",n)||"Notifications On":i("tabNotificationsOff",n)||"Off"})]}),e.jsxs("button",{className:"btn btn-sm btn-outline-secondary fullscreen-toggle-btn",onClick:()=>me(!1,!1),title:N?i("exitFullscreen",n):i("enterFullscreen",n),children:[e.jsx("i",{className:k("bi me-1",N?"bi-fullscreen-exit":"bi-fullscreen")}),N?i("exitFullscreen",n):i("enterFullscreen",n)]})]})]}),x.length>0&&e.jsxs("div",{className:k("workspace-tabs d-flex align-items-center border-bottom",N?"bg-white fullscreen-tabs":"bg-light"),style:{minHeight:"40px"},children:[e.jsxs("div",{className:"d-flex flex-grow-1",style:{overflowX:"auto",overflowY:"hidden"},children:[x.map(t=>{const s=je[t.id]||180;return e.jsxs("div",{"data-tab-id":t.id,className:k("workspace-tab d-flex align-items-center px-2 py-2 cursor-pointer","border-end position-relative",S===t.id&&"active bg-white"),onClick:()=>G(t.id),style:{width:`${s}px`,flexShrink:0,userSelect:"none"},children:[e.jsx("i",{className:k("bi me-2 flex-shrink-0",t.waitingForUser?"bi-bell-fill text-info":"bi-chat-dots text-muted")}),e.jsx("span",{className:k("text-truncate small flex-grow-1",t.waitingForUser&&"fw-semibold"),style:{minWidth:0},children:t.title}),t.waitingForUser&&S!==t.id&&e.jsx("span",{className:k("waiting-badge badge bg-info"),style:{fontSize:"0.65rem",padding:"0.2rem 0.4rem",marginLeft:"0.25rem",borderRadius:"50%",minWidth:"1.2rem",height:"1.2rem",display:"inline-flex",alignItems:"center",justifyContent:"center"},children:"●"}),e.jsxs("div",{className:"tab-actions d-flex align-items-center",children:[e.jsx("button",{className:"btn btn-sm btn-link p-0 text-muted tab-action-btn",onClick:a=>{a.stopPropagation(),Ce(t.id,a)},title:i("renameSession",n),tabIndex:-1,children:e.jsx("i",{className:"bi bi-pencil"})}),x.length>1&&e.jsx("button",{className:"btn btn-sm btn-link p-0 text-muted tab-action-btn",onClick:a=>Se(t.id,a),title:i("close",n),tabIndex:-1,children:e.jsx("i",{className:"bi bi-x"})})]}),e.jsx("div",{className:"tab-resize-handle",onMouseDown:a=>Ue(t.id,a),title:"Drag to resize"})]},t.id)}),e.jsx("button",{className:"btn btn-sm btn-link px-3 py-2 text-muted workspace-new-tab-btn",onClick:()=>fe(),title:i("newSession",n),style:{flexShrink:0,borderLeft:"1px solid rgba(0,0,0,0.1)"},children:e.jsx("i",{className:"bi bi-plus-lg"})})]}),N&&e.jsxs("button",{className:k("btn btn-sm px-3 py-1 mx-2",v?"btn-outline-primary":"btn-outline-secondary"),onClick:de,title:v?i("disableTabNotifications",n):i("enableTabNotifications",n),children:[e.jsx("i",{className:k("bi me-1",v?"bi-bell-fill":"bi-bell-slash")}),v?i("tabNotificationsOn",n):i("tabNotificationsOff",n)]}),N&&e.jsxs("button",{className:"btn btn-sm btn-outline-secondary px-3 py-1 mx-2",onClick:()=>me(!1,!1),title:i("exitFullscreen",n),children:[e.jsx("i",{className:"bi bi-fullscreen-exit me-1"}),i("exitFullscreen",n)]})]}),e.jsx("div",{className:"workspace-content flex-grow-1 position-relative",children:x.map(t=>e.jsxs("div",{className:k("position-absolute top-0 start-0 w-100 h-100",S===t.id?"d-block":"d-none"),children:[ve.has(t.id)&&e.jsx("div",{className:"position-absolute top-0 start-0 w-100 h-100 d-flex align-items-center justify-content-center bg-light",style:{zIndex:10},children:e.jsxs("div",{className:"text-center",children:[e.jsx("div",{className:"spinner-border text-primary mb-3",role:"status",children:e.jsx("span",{className:"visually-hidden",children:i("loading",n)})}),e.jsx("p",{className:"text-muted",children:i("workspaceLoading",n)})]})}),e.jsx("iframe",{ref:s=>{s?_.current.set(t.id,s):_.current.delete(t.id)},src:t.url,title:`Workspace - ${t.title}`,className:"w-100 h-100",style:{border:"none"},allow:"clipboard-read; clipboard-write",onLoad:()=>We(t.id)})]},t.id))}),e.jsxs(Oe,{isOpen:Ne,onClose:J,title:i("renameSession",n),size:"sm",children:[e.jsxs("div",{className:"mb-3",children:[e.jsx("label",{htmlFor:"rename-tab-input",className:"form-label",children:i("enterNewSessionName",n)}),e.jsx("input",{id:"rename-tab-input",type:"text",className:"form-control",value:W,onChange:t=>P(t.target.value),onKeyDown:t=>{t.key==="Enter"?ge():t.key==="Escape"&&J()},autoFocus:!0})]}),e.jsxs("div",{className:"d-flex justify-content-end gap-2",children:[e.jsx("button",{className:"btn btn-secondary",onClick:J,children:i("cancel",n)}),e.jsx("button",{className:"btn btn-primary",onClick:ge,disabled:!W.trim()||ce,children:ce?i("loading",n):i("save",n)})]})]}),e.jsx("style",{children:`
        .workspace-tab {
          transition: background-color 0.15s ease;
        }
        .workspace-tab:hover {
          background-color: rgba(0, 0, 0, 0.05);
        }
        .workspace-tab.active {
          border-bottom: 2px solid var(--primary, #0d6efd);
          margin-bottom: -1px;
        }
        .workspace-tab.active::after {
          content: '';
          position: absolute;
          bottom: 0;
          left: 0;
          right: 0;
          height: 2px;
          background: var(--primary, #0d6efd);
        }
        .workspace-tabs::-webkit-scrollbar {
          height: 4px;
        }
        .workspace-tabs::-webkit-scrollbar-thumb {
          background: #ccc;
          border-radius: 2px;
        }
        .workspace-tabs::-webkit-scrollbar-track {
          background: transparent;
        }
        /* Tab actions - show on hover or active */
        .tab-actions {
          opacity: 0;
          transition: opacity 0.15s ease;
        }
        .workspace-tab:hover .tab-actions,
        .workspace-tab.active .tab-actions {
          opacity: 1;
        }
        /* Tab action buttons - compact size */
        .tab-action-btn {
          line-height: 1;
          min-width: 20px;
          height: 20px;
          display: inline-flex;
          align-items: center;
          justify-content: center;
        }
        .tab-action-btn i {
          font-size: 0.8rem;
        }
        /* Tab resize handle */
        .tab-resize-handle {
          position: absolute;
          right: 0;
          top: 0;
          bottom: 0;
          width: 8px;
          cursor: col-resize;
          opacity: 0;
          transition: opacity 0.15s ease;
          border-right: 2px solid transparent;
        }
        .workspace-tab:hover .tab-resize-handle,
        .workspace-tab.active .tab-resize-handle {
          opacity: 0.5;
        }
        .tab-resize-handle:hover {
          opacity: 1;
          border-right-color: var(--primary, #0d6efd);
        }
        /* New tab button - matches tab style */
        .workspace-new-tab-btn {
          transition: background-color 0.15s ease;
        }
        .workspace-new-tab-btn:hover {
          background-color: rgba(0, 0, 0, 0.05) !important;
        }
        .workspace-new-tab-btn i {
          font-size: 1rem;
        }
        /* Loading progress steps */
        .workspace-loading {
          padding: 20px;
        }
        .progress-steps {
          display: flex;
          justify-content: center;
          gap: 20px;
        }
        .progress-step {
          display: flex;
          align-items: center;
          gap: 8px;
          padding: 8px 16px;
          border-radius: 8px;
          background: rgba(0, 0, 0, 0.05);
          opacity: 0.5;
          transition: opacity 0.3s ease, background-color 0.3s ease;
        }
        .progress-step.active {
          opacity: 1;
          background: rgba(13, 110, 253, 0.1);
        }
        .progress-step i {
          font-size: 1.2rem;
        }
        .progress-step.active i.bi-check-circle-fill {
          color: #28a745;
        }
        .progress-step.active i.bi-arrow-repeat {
          color: #0d6efd;
        }
        .progress-step span {
          font-size: 0.85rem;
        }
        .spin {
          animation: spin 1s linear infinite;
        }
        @keyframes spin {
          from { transform: rotate(0deg); }
          to { transform: rotate(360deg); }
        }
      `})]}):e.jsx("div",{className:"workspace",children:e.jsxs("div",{className:"text-center py-5",children:[e.jsx("i",{className:"bi bi-tools fs-1 text-muted"}),e.jsx("h4",{className:"mt-3",children:i("workspaceNotConfigured",n)}),e.jsx("p",{className:"text-muted",children:i("workspaceNotConfiguredHelp",n)})]})}):e.jsx("div",{className:"workspace",children:e.jsxs("div",{className:"text-center py-5",children:[e.jsx("i",{className:"bi bi-tools fs-1 text-muted"}),e.jsx("h4",{className:"mt-3",children:i("workspaceNotConfigured",n)}),e.jsx("p",{className:"text-muted",children:i("workspaceNotConfiguredHelp",n)})]})})};export{lt as Workspace};
//# sourceMappingURL=Workspace.CZmlZLjY.js.map
