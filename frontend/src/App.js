import React, { useEffect, useMemo, useRef, useState } from 'react';

const API_BASE = process.env.REACT_APP_API_BASE || 'http://localhost:8000';

function App() {
  const [connected, setConnected] = useState(false);
  const [verseHistory, setVerseHistory] = useState([]);
  const [verseIndex, setVerseIndex] = useState(-1);
  const [transcript, setTranscript] = useState('');
  const [speaker, setSpeaker] = useState('Pastor');
  const [recording, setRecording] = useState(false);
  const [accessState, setAccessState] = useState({ checked: false, valid: false, reason: '' });
  const [notesText, setNotesText] = useState('');
  const [summaryResult, setSummaryResult] = useState(null);
  const [view, setView] = useState('projector');

  const wsRef = useRef(null);
  const recognitionRef = useRef(null);

  const activeVerse = verseHistory[verseIndex] || null;

  useEffect(() => {
    const token = new URLSearchParams(window.location.search).get('token');
    if (!token) {
      setAccessState({ checked: true, valid: false, reason: 'Access denied' });
      return;
    }

    fetch(`${API_BASE}/validate_qr?token=${encodeURIComponent(token)}`)
      .then((res) => res.json())
      .then((res) => setAccessState({ checked: true, valid: res.valid, reason: res.reason }))
      .catch(() => setAccessState({ checked: true, valid: false, reason: 'Access denied' }));
  }, []);

  useEffect(() => {
    if (!accessState.valid) return;
    const ws = new WebSocket(API_BASE.replace('http', 'ws') + '/ws');
    wsRef.current = ws;
    ws.onopen = () => setConnected(true);
    ws.onclose = () => setConnected(false);
    ws.onerror = () => setConnected(false);
    ws.onmessage = (evt) => {
      const data = JSON.parse(evt.data);
      if (data.type === 'verse') {
        setVerseHistory((prev) => {
          const next = [...prev, data];
          setVerseIndex(next.length - 1);
          speakVerse(data.text);
          return next;
        });
      }
    };
    return () => ws.close();
  }, [accessState.valid]);

  useEffect(() => {
    const onKey = (event) => {
      if (event.key === 'ArrowLeft') {
        setVerseIndex((idx) => Math.max(0, idx - 1));
      }
      if (event.key === 'ArrowRight') {
        if (verseIndex < verseHistory.length - 1) {
          setVerseIndex((idx) => idx + 1);
        } else {
          const requested = window.prompt('Reference (ex: Matthew 1:1)?');
          if (requested) {
            fetch(`${API_BASE}/scripture?reference=${encodeURIComponent(requested)}&speaker=${encodeURIComponent(speaker)}`);
          }
        }
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [verseHistory.length, verseIndex, speaker]);

  useEffect(() => {
    if (!window.SpeechRecognition && !window.webkitSpeechRecognition) return;
    const Rec = window.SpeechRecognition || window.webkitSpeechRecognition;
    const recognition = new Rec();
    recognition.continuous = true;
    recognition.interimResults = false;
    recognition.onresult = (event) => {
      const chunk = event.results[event.results.length - 1][0].transcript;
      setTranscript((prev) => `${prev} ${chunk}`.trim());
      fetch(`${API_BASE}/transcript`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ text: chunk, speaker })
      });
    };
    recognitionRef.current = recognition;
  }, [speaker]);

  const visibleWords = useMemo(() => {
    if (!activeVerse?.text) return [];
    return activeVerse.text.split(/\s+/);
  }, [activeVerse]);

  const startRecording = () => {
    if (!recognitionRef.current) return;
    recognitionRef.current.start();
    setRecording(true);
  };

  const stopRecording = () => {
    if (!recognitionRef.current) return;
    recognitionRef.current.stop();
    setRecording(false);
  };

  const requestSummary = async () => {
    const res = await fetch(`${API_BASE}/notes/summary`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text: notesText })
    });
    setSummaryResult(await res.json());
  };

  if (!accessState.checked) return <div className="screen">Checking access...</div>;
  if (!accessState.valid) return <div className="screen deny">Access denied ({accessState.reason})</div>;

  return (
    <div className={`app ${view}`}>
      <div className="watermark">Providence Baptist Church</div>
      <header>
        <h1>Smart Worship System</h1>
        <div className={`chip ${connected ? 'on' : 'off'}`}>{connected ? 'Connected' : 'Disconnected'}</div>
      </header>

      <div className="tabs">
        <button onClick={() => setView('projector')}>Projector</button>
        <button onClick={() => setView('mobile')}>Mobile</button>
        <button onClick={() => setView('notes')}>Notes</button>
      </div>

      {view !== 'notes' && (
        <main className="verse-panel">
          <h2>{activeVerse?.reference || 'Awaiting scripture...'}</h2>
          <p className="verse-text">
            {visibleWords.map((word, i) => (
              <span key={`${word}-${i}`} style={{ animationDelay: `${i * 70}ms` }} className="word-reveal">{word} </span>
            ))}
          </p>
        </main>
      )}

      {view === 'notes' && (
        <section className="notes">
          <textarea value={notesText} onChange={(e) => setNotesText(e.target.value)} placeholder="Paste transcript notes..." />
          <button onClick={requestSummary}>Generate Summary</button>
          {summaryResult && (
            <pre>{JSON.stringify(summaryResult, null, 2)}</pre>
          )}
        </section>
      )}

      <footer>
        <input value={speaker} onChange={(e) => setSpeaker(e.target.value)} placeholder="Speaker" />
        <button onClick={startRecording} disabled={recording}>Start Recording</button>
        <button onClick={stopRecording} disabled={!recording}>Stop Recording</button>
      </footer>
    </div>
  );
}

function speakVerse(text) {
  if (!window.speechSynthesis || !text) return;
  window.speechSynthesis.cancel();
  const utterance = new SpeechSynthesisUtterance(text);
  utterance.rate = 0.95;
  window.speechSynthesis.speak(utterance);
}

export default App;
