/* ===== Particles ===== */
(function(){
  const canvas=document.getElementById('particles-canvas'), ctx=canvas.getContext('2d');
  let P=[];
  function resize(){canvas.width=window.innerWidth;canvas.height=window.innerHeight;}
  function mk(){return{x:Math.random()*canvas.width,y:Math.random()*canvas.height,
    vx:(Math.random()-.5)*.4,vy:(Math.random()-.5)*.4,
    size:Math.random()*1.5+.5,opacity:Math.random()*.5+.1,
    color:Math.random()>.5?'139,92,246':'6,182,212'};}
  function draw(){
    ctx.clearRect(0,0,canvas.width,canvas.height);
    P.forEach(p=>{ctx.beginPath();ctx.arc(p.x,p.y,p.size,0,Math.PI*2);
      ctx.fillStyle=`rgba(${p.color},${p.opacity})`;ctx.fill();
      p.x+=p.vx;p.y+=p.vy;
      if(p.x<0)p.x=canvas.width;if(p.x>canvas.width)p.x=0;
      if(p.y<0)p.y=canvas.height;if(p.y>canvas.height)p.y=0;});
    for(let i=0;i<P.length;i++)for(let j=i+1;j<P.length;j++){
      const dx=P[i].x-P[j].x,dy=P[i].y-P[j].y,d=Math.sqrt(dx*dx+dy*dy);
      if(d<100){ctx.beginPath();ctx.strokeStyle=`rgba(139,92,246,${.08*(1-d/100)})`;
        ctx.lineWidth=.5;ctx.moveTo(P[i].x,P[i].y);ctx.lineTo(P[j].x,P[j].y);ctx.stroke();}}
    requestAnimationFrame(draw);}
  window.addEventListener('resize',resize);resize();
  P=Array.from({length:60},mk);draw();
})();

/* ===== State ===== */
let videoInfo=null, playlistInfo=null;
let selectedQIdx=null, selectedPLQ='720p';
let currentUrl='', currentMode='video';

/* ===== Formatters ===== */
const fmtDur=s=>{if(!s)return'';const h=Math.floor(s/3600),m=Math.floor((s%3600)/60),sc=s%60;
  return h>0?`${h}:${String(m).padStart(2,'0')}:${String(sc).padStart(2,'0')}`:`${m}:${String(sc).padStart(2,'0')}`;};
const fmtViews=n=>{if(!n)return'N/A';if(n>=1e9)return`${(n/1e9).toFixed(1)}B views`;
  if(n>=1e6)return`${(n/1e6).toFixed(1)}M views`;if(n>=1e3)return`${(n/1e3).toFixed(1)}K views`;return`${n} views`;};
const fmtSize=b=>{if(!b)return'';if(b>=1073741824)return` ~${(b/1073741824).toFixed(1)}GB`;
  if(b>=1048576)return` ~${(b/1048576).toFixed(0)}MB`;return` ~${(b/1024).toFixed(0)}KB`;};
const safeName=s=>s.replace(/[<>:"/\\|?*\x00-\x1f]/g,'_').replace(/\.+$/,'').trim()||'video';
const isPlaylist=u=>u.includes('/playlist?')||(u.includes('list=')&&!u.includes('watch?v='));
const escHtml=s=>s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');

/* ===== UI ===== */
function showError(msg){
  document.getElementById('error-text').textContent=msg;
  document.getElementById('error-box').style.display='flex';
  setTimeout(()=>document.getElementById('error-box').style.display='none',9000);}
function hideError(){document.getElementById('error-box').style.display='none';}

function setLoading(show,msg='Fetching info…'){
  document.getElementById('loading-container').style.display=show?'flex':'none';
  document.querySelector('.loading-text').childNodes[0].textContent=msg;
  document.getElementById('fetch-btn').disabled=show;}

function setDownloading(show,title='Processing…',sub='Please wait'){
  document.getElementById('progress-container').style.display=show?'block':'none';
  document.getElementById('progress-title').textContent=title;
  document.getElementById('progress-sub').textContent=sub;
  document.getElementById('download-btn').disabled=show;
  document.getElementById('playlist-dl-btn').disabled=show;
  document.getElementById('download-btn-text').textContent=show?'Processing…':'Download';
  document.getElementById('playlist-dl-text').textContent=show?'Processing…':'Download All as ZIP';}

function hideCards(){
  ['video-card','playlist-card','progress-container','features-section']
    .forEach(id=>document.getElementById(id).style.display='none');}

/* ===== 2-Step Download (shows in Chrome download bar) =====
   Step 1: POST /api/prepare  → server downloads from YouTube, returns file_id
   Step 2: window.open /api/file/<file_id> → browser triggers download with progress bar
*/
async function startDownload(prepareEndpoint, body, label) {
  hideError();
  setDownloading(true,
    `⏳ ${label}`,
    'Downloading from YouTube — this takes 20–60 seconds…');
  try {
    // Step 1 — server prepares file
    const res  = await fetch(prepareEndpoint, {
      method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify(body)
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || 'Download failed');

    // Step 2 — trigger browser download via <a> GET request
    // This shows immediately in Chrome's download bar with full progress!
    const a = document.createElement('a');
    a.href     = `/api/file/${data.file_id}`;
    a.download = data.filename;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);

  } catch(err) {
    showError(err.message || 'Download failed. Please try again.');
  } finally {
    setDownloading(false);
  }
}

/* ===== Fetch ===== */
async function fetchInfo(){
  const url=document.getElementById('url-input').value.trim();
  if(!url){showError('Please paste a YouTube URL!');return;}
  if(!url.includes('youtube.com')&&!url.includes('youtu.be')){showError('Enter a valid YouTube URL.');return;}
  hideError(); currentUrl=url; hideCards();
  try{
    if(isPlaylist(url)){currentMode='playlist';setLoading(true,'Loading playlist…');await fetchPlaylistInfo(url);}
    else{currentMode='video';setLoading(true,'Fetching video info…');await fetchVideoInfo(url);}
  }catch(e){showError(e.message||'Something went wrong.');document.getElementById('features-section').style.display='block';}
  finally{setLoading(false);}
}

/* ===== Single Video ===== */
async function fetchVideoInfo(url){
  const res=await fetch('/api/info',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({url})});
  const data=await res.json();
  if(!res.ok)throw new Error(data.error||'Failed to fetch video info');
  videoInfo=data; renderVideoCard(data);}

function renderVideoCard(info){
  document.getElementById('video-thumbnail').src        =info.thumbnail||'';
  document.getElementById('video-title').textContent    =info.title||'Unknown';
  document.getElementById('video-uploader').textContent =info.uploader||'Unknown';
  document.getElementById('video-views').textContent    =fmtViews(info.view_count);
  document.getElementById('duration-badge').textContent =fmtDur(info.duration);

  const grid=document.getElementById('quality-grid');
  grid.innerHTML=''; selectedQIdx=null;
  const labels={'1080p':'FHD','720p':'HD','480p':'SD','360p':'Low'};

  // ⭐ Best Quality chip
  const bc=document.createElement('button');
  bc.className='quality-chip best-chip'; bc.dataset.index='best';
  bc.innerHTML='<span class="chip-text">⭐ Best Quality</span>';
  bc.addEventListener('click',()=>{
    grid.querySelectorAll('.quality-chip').forEach(c=>c.classList.remove('selected'));
    bc.classList.add('selected'); selectedQIdx='best';});
  grid.appendChild(bc);

  info.formats.forEach((fmt,idx)=>{
    const chip=document.createElement('button');
    chip.className='quality-chip'; chip.dataset.index=idx;
    const badge=labels[fmt.quality]||'', size=fmt.filesize?`<span class="chip-badge">${fmtSize(fmt.filesize)}</span>`:'';
    chip.innerHTML=`<span class="chip-text">${fmt.quality}${badge?` <small>${badge}</small>`:''}</span>${size}`;
    chip.addEventListener('click',()=>{
      grid.querySelectorAll('.quality-chip').forEach(c=>c.classList.remove('selected'));
      chip.classList.add('selected'); selectedQIdx=idx;});
    grid.appendChild(chip);
    if(fmt.quality==='720p')chip.click();
  });
  if(selectedQIdx===null)bc.click();
  document.getElementById('video-card').style.display='block';}

async function downloadSingle(){
  if(!videoInfo||selectedQIdx===null){showError('Fetch a video first!');return;}
  const isBest =selectedQIdx==='best';
  const fmt    =isBest?null:videoInfo.formats[selectedQIdx];
  const isAudio=!isBest&&fmt.ext==='mp3';
  const quality=isBest?'best':fmt.quality;
  await startDownload('/api/prepare',
    {url:currentUrl, quality, is_audio:isAudio},
    isAudio?'Extracting audio…':`Preparing ${quality} video…`);
}

/* ===== Playlist ===== */
async function fetchPlaylistInfo(url){
  const res=await fetch('/api/playlist-info',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({url})});
  const data=await res.json();
  if(!res.ok)throw new Error(data.error||'Failed to fetch playlist');
  playlistInfo=data; renderPlaylistCard(data);}

function renderPlaylistCard(info){
  document.getElementById('playlist-title').textContent=info.playlist_title||'Playlist';
  document.getElementById('playlist-count').textContent=info.video_count;
  const list=document.getElementById('playlist-list');
  list.innerHTML='';
  (info.videos||[]).slice(0,10).forEach((v,i)=>{
    const item=document.createElement('div');
    item.className='playlist-item';
    item.innerHTML=`<div class="pl-num">${String(i+1).padStart(2,'0')}</div>
      <div class="pl-thumb">${v.thumbnail?`<img src="${v.thumbnail}" alt=""/>`:'<div class="pl-thumb-placeholder"></div>'}</div>
      <div class="pl-info"><p class="pl-title">${escHtml(v.title)}</p>${v.duration?`<span class="pl-dur">${fmtDur(v.duration)}</span>`:''}</div>`;
    list.appendChild(item);});
  if(info.video_count>10){
    const m=document.createElement('div');m.className='playlist-more';
    m.textContent=`+ ${info.video_count-10} more videos in ZIP`;list.appendChild(m);}
  document.getElementById('playlist-card').style.display='block';}

function selectPlaylistQuality(el){
  document.querySelectorAll('#playlist-quality-grid .quality-chip').forEach(c=>c.classList.remove('selected'));
  el.classList.add('selected'); selectedPLQ=el.dataset.q;}

async function downloadPlaylist(){
  if(!playlistInfo){showError('Fetch a playlist first!');return;}
  await startDownload('/api/prepare-playlist',
    {url:currentUrl, quality:selectedPLQ},
    `Downloading ${playlistInfo.video_count} videos…`);}

/* ===== Input events ===== */
document.getElementById('url-input').addEventListener('keydown',e=>{if(e.key==='Enter')fetchInfo();});
document.getElementById('url-input').addEventListener('paste',()=>{
  setTimeout(()=>{const v=document.getElementById('url-input').value.trim();
    if(v.includes('youtube.com')||v.includes('youtu.be'))fetchInfo();},150);});
