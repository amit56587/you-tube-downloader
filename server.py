import os, re, json, subprocess, sys, zipfile, tempfile, shutil, uuid, time, threading
import atexit
from flask import Flask, request, jsonify, send_file, after_this_request
from flask_cors import CORS

app = Flask(__name__, static_folder='public', static_url_path='')
CORS(app)
PORT = int(os.environ.get('PORT', 5000))

# Folder where prepared files wait to be picked up by the browser
SERVE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), '_serving')
os.makedirs(SERVE_DIR, exist_ok=True)

# In-memory registry of prepared downloads: {file_id: {path, filename, dir, created}}
_prepared = {}
_lock     = threading.Lock()

def _cleanup_old_files():
    """Remove prepared files that were never fetched (older than 15 min)."""
    while True:
        time.sleep(300)
        now = time.time()
        with _lock:
            expired = [fid for fid, info in _prepared.items() if now - info['created'] > 900]
        for fid in expired:
            with _lock:
                info = _prepared.pop(fid, None)
            if info:
                shutil.rmtree(info['dir'], ignore_errors=True)

threading.Thread(target=_cleanup_old_files, daemon=True).start()
atexit.register(lambda: shutil.rmtree(SERVE_DIR, ignore_errors=True))

# ── ffmpeg discovery ──────────────────────────────────────
def find_ffmpeg():
    p = shutil.which('ffmpeg')
    if p: return p
    try:
        import imageio_ffmpeg
        p = imageio_ffmpeg.get_ffmpeg_exe()
        if p and os.path.exists(p): return p
    except Exception: pass
    local = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'ffmpeg.exe')
    if os.path.exists(local): return local
    for root, dirs, files in os.walk(os.path.dirname(os.path.abspath(__file__))):
        dirs[:] = [d for d in dirs if d not in ('_serving','downloads','public','__pycache__')]
        for f in files:
            if f in ('ffmpeg.exe','ffmpeg'): return os.path.join(root, f)
    return None

FFMPEG_PATH = find_ffmpeg()
HAS_FFMPEG  = FFMPEG_PATH is not None
print(f"ffmpeg: {'✅ ' + FFMPEG_PATH if HAS_FFMPEG else '❌ not found'}")

# ── YouTube cookies (bypasses bot detection on cloud servers) ────
COOKIES_FILE = None
_yt_cookies = os.environ.get('YT_COOKIES', '').strip()
if _yt_cookies:
    COOKIES_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'yt_cookies.txt')
    with open(COOKIES_FILE, 'w', encoding='utf-8') as _f:
        _f.write(_yt_cookies)
    print('✅ YouTube cookies loaded from environment')
else:
    print('⚠️  No YT_COOKIES env var — some videos may require login')

BROWSER_HEADERS = [
    '--user-agent', 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36',
    '--add-header', 'Accept-Language:en-US,en;q=0.9',
    # Use Android + web player clients — bypasses bot detection on cloud IPs
    '--extractor-args', 'youtube:player_client=android,web',
    '--no-check-certificates',
]

def ytdlp(*args):
    extra = ['--ffmpeg-location', FFMPEG_PATH] if HAS_FFMPEG else []
    cookies = ['--cookies', COOKIES_FILE] if COOKIES_FILE else []
    return [sys.executable, '-m', 'yt_dlp'] + extra + cookies + BROWSER_HEADERS + list(args)

def fmt_for(quality):
    if quality == 'best':
        return 'bestvideo+bestaudio/best' if HAS_FFMPEG else 'best'
    h = quality.replace('p','')
    if HAS_FFMPEG:
        return (f'bestvideo[height<={h}]+bestaudio'
                f'/bestvideo[height<={h}]+bestaudio[ext=m4a]'
                f'/best[height<={h}]')
    return f'best[height<={h}][ext=mp4]/best[height<={h}]/best'

def find_media(directory):
    for f in os.listdir(directory):
        if any(f.lower().endswith(e) for e in ('.mp4','.mkv','.webm','.mp3','.m4a')):
            return os.path.join(directory, f)
    return None

def clean_err(stderr):
    for line in reversed(stderr.splitlines()):
        if 'ERROR' in line:
            return line.split('ERROR:')[-1].strip()
    return (stderr.strip().splitlines() or ['Unknown error'])[-1]

# ── Static ────────────────────────────────────────────────
@app.route('/')
def index():
    return app.send_static_file('index.html')

# ── Video info ────────────────────────────────────────────
@app.route('/api/info', methods=['POST'])
def get_info():
    url = (request.get_json() or {}).get('url','').strip()
    if not url: return jsonify({'error':'No URL'}), 400
    try:
        r = subprocess.run(ytdlp('--dump-json','--no-playlist', url),
                           capture_output=True, text=True, timeout=45)
        if not r.stdout.strip():
            return jsonify({'error': clean_err(r.stderr) or 'Could not fetch info'}), 400
        info = json.loads(r.stdout)
        return jsonify({
            'type':'video', 'title':info.get('title','Unknown'),
            'thumbnail':info.get('thumbnail',''), 'duration':info.get('duration',0),
            'uploader':info.get('uploader','Unknown'), 'view_count':info.get('view_count',0),
            'formats': _build_formats(info.get('formats',[]))
        })
    except subprocess.TimeoutExpired: return jsonify({'error':'Timed out'}), 408
    except Exception as e:            return jsonify({'error':str(e)}), 500

# ── Playlist info ─────────────────────────────────────────
@app.route('/api/playlist-info', methods=['POST'])
def get_playlist_info():
    url = (request.get_json() or {}).get('url','').strip()
    if not url: return jsonify({'error':'No URL'}), 400
    try:
        r = subprocess.run(ytdlp('--flat-playlist','--dump-json','--yes-playlist', url),
                           capture_output=True, text=True, timeout=90)
        videos = []
        for line in r.stdout.splitlines():
            try:
                e = json.loads(line.strip())
                thumb = (e.get('thumbnails') or [{}])[-1].get('url','') or e.get('thumbnail','')
                videos.append({'id':e.get('id',''), 'title':e.get('title','Unknown'),
                               'duration':e.get('duration',0), 'thumbnail':thumb,
                               'url':f"https://www.youtube.com/watch?v={e.get('id','')}"})
            except: pass
        if not videos: return jsonify({'error':'No videos found'}), 400
        title = 'Playlist'
        try:
            r2 = subprocess.run(ytdlp('--flat-playlist','--print','playlist_title','--playlist-items','1', url),
                                capture_output=True, text=True, timeout=20)
            if r2.returncode == 0: title = r2.stdout.strip().splitlines()[0] or title
        except: pass
        return jsonify({'type':'playlist','playlist_title':title,
                        'video_count':len(videos),'videos':videos})
    except subprocess.TimeoutExpired: return jsonify({'error':'Timed out'}), 408
    except Exception as e:            return jsonify({'error':str(e)}), 500

# ── STEP 1: Prepare download (server downloads from YouTube) ──
@app.route('/api/prepare', methods=['POST'])
def prepare_download():
    data     = request.get_json() or {}
    url      = data.get('url','').strip()
    quality  = data.get('quality','720p')
    is_audio = data.get('is_audio', False)
    if not url: return jsonify({'error':'No URL'}), 400

    job_dir = tempfile.mkdtemp(dir=SERVE_DIR)
    try:
        out_tmpl = os.path.join(job_dir, '%(title)s.%(ext)s')

        if is_audio:
            cmd = ytdlp('-f','bestaudio/best',
                        '--extract-audio','--audio-format','mp3','--audio-quality','0',
                        '-o', out_tmpl, '--no-playlist','--no-part', url)
        else:
            cmd = ytdlp('-f', fmt_for(quality),
                        '--merge-output-format','mp4',
                        '-o', out_tmpl, '--no-playlist','--no-part', url)

        r = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        filepath = find_media(job_dir)

        if filepath is None:
            shutil.rmtree(job_dir, ignore_errors=True)
            return jsonify({'error': clean_err(r.stderr) or 'Download failed'}), 500

        file_id  = str(uuid.uuid4())
        filename = os.path.basename(filepath)
        with _lock:
            _prepared[file_id] = {'path': filepath, 'filename': filename,
                                  'dir': job_dir, 'created': time.time()}
        return jsonify({'file_id': file_id, 'filename': filename})

    except subprocess.TimeoutExpired:
        shutil.rmtree(job_dir, ignore_errors=True)
        return jsonify({'error':'Download timed out. Try a lower quality.'}), 408
    except Exception as e:
        shutil.rmtree(job_dir, ignore_errors=True)
        return jsonify({'error': str(e)}), 500

# ── STEP 2: Browser fetches prepared file (shows in Chrome download bar) ──
@app.route('/api/file/<file_id>')
def get_file(file_id):
    with _lock:
        info = _prepared.get(file_id)
    if not info or not os.path.exists(info['path']):
        return "File not found or expired", 404

    ext  = os.path.splitext(info['filename'])[1].lower()
    mime = 'audio/mpeg' if ext == '.mp3' else 'video/mp4'

    @after_this_request
    def cleanup(resp):
        with _lock:
            _prepared.pop(file_id, None)
        shutil.rmtree(info['dir'], ignore_errors=True)
        return resp

    return send_file(info['path'], mimetype=mime,
                     as_attachment=True, download_name=info['filename'],
                     conditional=True)

# ── Playlist download (ZIP) ───────────────────────────────
@app.route('/api/prepare-playlist', methods=['POST'])
def prepare_playlist():
    data    = request.get_json() or {}
    url     = data.get('url','').strip()
    quality = data.get('quality','720p')
    if not url: return jsonify({'error':'No URL'}), 400

    job_dir = tempfile.mkdtemp(dir=SERVE_DIR)
    try:
        out_tmpl = os.path.join(job_dir, '%(playlist_index)02d - %(title)s.%(ext)s')
        cmd = ytdlp('-f', fmt_for(quality),
                    '--merge-output-format','mp4',
                    '-o', out_tmpl,
                    '--yes-playlist','--no-part','--ignore-errors', url)
        subprocess.run(cmd, capture_output=True, text=True, timeout=3600)

        media = [f for f in os.listdir(job_dir)
                 if any(f.lower().endswith(e) for e in ('.mp4','.mkv','.webm','.mp3'))]
        if not media:
            shutil.rmtree(job_dir, ignore_errors=True)
            return jsonify({'error':'No videos downloaded'}), 500

        zip_path = os.path.join(job_dir, 'playlist.zip')
        with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_STORED) as zf:
            for f in sorted(media):        # sorted = serial order in ZIP
                zf.write(os.path.join(job_dir, f), f)

        file_id = str(uuid.uuid4())
        with _lock:
            _prepared[file_id] = {'path': zip_path, 'filename': 'playlist.zip',
                                  'dir': job_dir, 'created': time.time()}
        return jsonify({'file_id': file_id, 'filename': 'playlist.zip'})

    except subprocess.TimeoutExpired:
        shutil.rmtree(job_dir, ignore_errors=True)
        return jsonify({'error':'Timed out'}), 408
    except Exception as e:
        shutil.rmtree(job_dir, ignore_errors=True)
        return jsonify({'error':str(e)}), 500

# ── Format builder ────────────────────────────────────────
def _build_formats(raw):
    seen, out = set(), []
    for f in raw:
        h = f.get('height')
        if f.get('vcodec','none') != 'none' and h and h not in seen and h in (360,480,720,1080):
            seen.add(h)
            out.append({'quality':f'{h}p','height':h,'ext':'mp4',
                        'filesize':f.get('filesize') or f.get('filesize_approx')})
    out.sort(key=lambda x: x['height'], reverse=True)
    for h in (1080,720,480,360):
        if h not in {f['height'] for f in out}:
            out.append({'quality':f'{h}p','height':h,'ext':'mp4','filesize':None})
    out.sort(key=lambda x: x['height'], reverse=True)
    out.append({'quality':'Audio Only (MP3)','height':0,'ext':'mp3','filesize':None})
    return out

if __name__ == '__main__':
    print(f"\n🚀 TubeSnap on port {PORT}  |  ffmpeg: {'✅' if HAS_FFMPEG else '❌'}")
    print(f"🌐 http://localhost:{PORT}\n")
    app.run(debug=False, host='0.0.0.0', port=PORT, threaded=True)
