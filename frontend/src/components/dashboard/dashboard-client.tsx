"use client";

import React, { useState, useEffect, useRef, useCallback } from "react";
import dynamic from "next/dynamic";
import { motion, AnimatePresence } from "framer-motion";
import { Badge } from "@/components/ui/badge";
import {
  Code2, Zap, Server, Clock, Eye, X, CheckCircle, AlertTriangle,
  FileCode, Search, ChevronLeft, ChevronRight, Lock, User, LogOut,
  Cpu, Activity, RotateCcw, Play, Bot, Send, Sparkles, FolderUp, FileText, UploadCloud, File as FileIcon, Trash2,
  Shield, TerminalSquare, Loader2, GitCompare,
  AlertCircle, Image as ImageIcon, Table2, Layers, Cpu as NodeIcon, MousePointer2, Box, Globe, Workflow, Settings2, Power, History
} from "lucide-react";

const MonacoEditor = dynamic(() => import("@monaco-editor/react"), { ssr: false });

const WORKERS = [
  { id: "worker1", url: "http://localhost:5002", display: "Core A-1", appPort: 8001 },
  { id: "worker2", url: "http://localhost:5003", display: "Core B-2", appPort: 8002 },
  { id: "worker3", url: "http://localhost:5004", display: "Core C-3", appPort: 8003 },
];

const AGENTS = [
  { id: "planner", name: "Architect", color: "#4285f4", icon: Layers },
  { id: "coder", name: "Synthesizer", color: "#10a37f", icon: Bot },
  { id: "evaluator", name: "Evaluator", color: "#f55036", icon: Cpu },
  { id: "debugger", name: "Debugger", color: "#7c3aed", icon: Zap },
];

const MODELS = [
  { id: "gpt-4o-mini", name: "GPT-4o Mini", provider: "OpenAI" },
  { id: "gpt-4o", name: "GPT-4o", provider: "OpenAI" },
  { id: "llama-3.3-70b", name: "Llama 3.3 70B", provider: "Groq" },
  { id: "llama-3.1-8b", name: "Llama 3.1 8B", provider: "Groq" },
  { id: "gemini-2.0-flash", name: "Gemini 2.0 Flash", provider: "Google" },
  { id: "mixtral-8x7b", name: "Mixtral 8x7B", provider: "Groq" },
];

const Card3D = ({ children, className = "" }: { children: React.ReactNode; className?: string }) => {
  const [rotate, setRotate] = useState({ x: 0, y: 0 });
  const ref = useRef<HTMLDivElement>(null);
  const handleMouseMove = (e: React.MouseEvent) => {
    if (!ref.current) return;
    const rect = ref.current.getBoundingClientRect();
    const x = ((e.clientX - rect.left) / rect.width - 0.5) * 15;
    const y = ((e.clientY - rect.top) / rect.height - 0.5) * -15;
    setRotate({ x, y });
  };
  return (
    <motion.div
      ref={ref} className={`relative group will-change-transform ${className}`}
      onMouseMove={handleMouseMove} onMouseLeave={() => setRotate({ x: 0, y: 0 })}
      animate={{ rotateX: rotate.x, rotateY: rotate.y }} transition={{ type: "spring", stiffness: 300, damping: 30 }}
      style={{ transformStyle: "preserve-3d" }}
    >
      {children}
    </motion.div>
  );
};

// ─── Swarm Progress Visualization ───────────────────────────────────────────

function SwarmNode({ agent, status }: { agent: any; status: "idle" | "running" | "completed" | "failed" | "warning" }) {
  const colors = {
    idle: "border-white/5 bg-white/5 opacity-50",
    running: "border-cyan-500 bg-cyan-500/20 animate-pulse shadow-[0_0_15px_rgba(6,182,212,0.4)]",
    completed: "border-emerald-500 bg-emerald-500/20 shadow-[0_0_15px_rgba(16,185,129,0.4)]",
    failed: "border-red-500 bg-red-500/20 shadow-[0_0_15px_rgba(239,68,68,0.4)]",
    warning: "border-yellow-500 bg-yellow-500/20 animate-bounce shadow-[0_0_15px_rgba(234,179,8,0.4)]"
  };

  return (
    <div className="flex flex-col items-center gap-2">
      <div className={`w-12 h-12 rounded-2xl border flex items-center justify-center transition-all duration-500 ${colors[status]}`}>
        <agent.icon className={`w-6 h-6 ${status === "idle" ? "text-zinc-600" : "text-white"}`} />
      </div>
      <span className="text-[10px] font-black uppercase tracking-tighter text-zinc-500">{agent.name}</span>
    </div>
  );
}

function Pipeline({ activeAgent, statuses }: { activeAgent: string | null; statuses: Record<string, any> }) {
  return (
    <div className="flex items-center justify-center gap-4 px-6 py-8 bg-black/40 rounded-3xl border border-white/5 relative overflow-hidden">
      <div className="absolute inset-0 bg-gradient-to-r from-cyan-500/5 via-transparent to-purple-500/5" />
      {AGENTS.map((agent, i) => (
        <React.Fragment key={agent.id}>
          <SwarmNode agent={agent} status={statuses[agent.id]?.status || (activeAgent === agent.id ? "running" : "idle")} />
          {i < AGENTS.length - 1 && (
            <div className="w-12 h-[2px] bg-white/5 relative overflow-hidden">
              <motion.div
                animate={{ left: ["-100%", "100%"] }} transition={{ duration: 1.5, repeat: Infinity, ease: "linear" }}
                className={`absolute inset-0 w-1/2 bg-gradient-to-r from-transparent via-cyan-400 to-transparent ${activeAgent === agent.id ? 'opacity-100' : 'opacity-0'}`}
              />
            </div>
          )}
        </React.Fragment>
      ))}
    </div>
  );
}

export function DashboardClient() {
  const [activeWorker, setActiveWorker] = useState(WORKERS[0]);
  const [selectedModel, setSelectedModel] = useState("gpt-4o-mini");
  const [user, setUser] = useState<{ user_id: string } | null>(null);
  const [code, setCode] = useState("");
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [output, setOutput] = useState<any>(null);
  const [swarmEvents, setSwarmEvents] = useState<any[]>([]);
  const [agentStatuses, setAgentStatuses] = useState<Record<string, any>>({});
  const [enabledAgents, setEnabledAgents] = useState<string[]>(["planner", "coder", "evaluator", "debugger"]);
  const [activeAgent, setActiveAgent] = useState<string | null>(null);
  const [lockOwner, setLockOwner] = useState<string | null>(null);
  const [tab, setTab] = useState<"process" | "output" | "services">("process");
  const [servicesList, setServicesList] = useState<any[]>([]);

  useEffect(() => {
    let iv: any;
    if (tab === "services" && user) {
      const fetchSvc = async () => {
        const results = await Promise.allSettled(
          WORKERS.map(w =>
            fetch(`${w.url}/services?user_id=${user.user_id}`)
              .then(r => r.json())
              .then((d: any) => (d.services || []).map((s: any) => ({ ...s, appPort: w.appPort })))
          )
        );
        setServicesList(results.flatMap(r => r.status === "fulfilled" ? r.value : []));
      };
      fetchSvc();
      iv = setInterval(fetchSvc, 2000);
    }
    return () => clearInterval(iv);
  }, [tab, user]);

  const deployCode = async () => {
    setIsSubmitting(true);
    try {
      const service_id = crypto.randomUUID();
      for (const worker of WORKERS) {
        try {
          const resp = await fetch(`${worker.url}/services/start`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ service_id, user_id: user?.user_id, code, port: 8000 }),
          });
          if (resp.ok) break;
        } catch { /* worker unreachable, try next */ }
      }
      setTab("services");
    } finally { setIsSubmitting(false); }
  };
  
  const stopService = async (service_id: string, worker_id: string) => {
    const worker = WORKERS.find(w => w.id === worker_id);
    const url = worker ? worker.url : activeWorker.url;
    await fetch(`${url}/services/stop/${service_id}`, { method: "DELETE" });
  };
  const [prompt, setPrompt] = useState("");

  useEffect(() => {
    const u = localStorage.getItem("sandpy_user");
    setUser(u ? JSON.parse(u) : { user_id: "Operator-" + Math.floor(Math.random() * 1000) });
  }, []);

  const apiCall = async (endpoint: string, options: any = {}) => {
    try {
      const res = await fetch(`${activeWorker.url}${endpoint}`, {
        ...options, headers: { "Content-Type": "application/json", ...options.headers }
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.detail?.error || (typeof err.detail === 'string' ? err.detail : null) || res.statusText || "Communication Failure");
      }
      return res.json();
    } catch (e: any) {
      console.warn("Worker execution error:", e);
      // Fallback logic for cluster resilience
      if (endpoint !== "/lock") { // Don't fallback for locks to avoid state desync
        for (const w of WORKERS) {
          if (w.id === activeWorker.id) continue;
          try {
            const res = await fetch(`${w.url}${endpoint}`, {
              ...options, headers: { "Content-Type": "application/json", ...options.headers }
            });
            if (res.ok) {
              setActiveWorker(w);
              return res.json();
            }
          } catch { }
        }
      }
      throw e;
    }
  };

  const startSwarm = async () => {
    if (!prompt.trim() || isSubmitting) return;
    setIsSubmitting(true); setSwarmEvents([]); setAgentStatuses({}); setActiveAgent(null); setTab("process");
    
    const finish = () => { setIsSubmitting(false); setActiveAgent(null); };
    
    try {
      const res = await apiCall("/ai/swarm/run", {
        method: "POST", body: JSON.stringify({ user_id: user?.user_id, prompt, model: selectedModel, enabled_agents: enabledAgents })
      });
      
      const wsUrl = `${activeWorker.url.replace('http', 'ws')}/ws/job/${res.job_id}`;
      const ws = new WebSocket(wsUrl);
      
      // Safety timeout – finish after 5 mins max
      const timeout = setTimeout(() => { finish(); ws.close(); }, 300_000);
      
      ws.onmessage = (e) => {
        const data = JSON.parse(e.data);
        if (data.type === "swarm_event") {
          setActiveAgent(data.role);
          setAgentStatuses(p => ({ ...p, [data.role]: { status: data.status, message: data.message } }));
          setSwarmEvents(p => [...p, data]);
        }
        if (data.type === "swarm_complete") {
          clearTimeout(timeout);
          finish();
          if (data.final_code) setCode(data.final_code);
          ws.close();
        }
      };
      ws.onerror = () => { clearTimeout(timeout); finish(); };
      ws.onclose = () => { clearTimeout(timeout); finish(); };
    } catch { finish(); }
  };

  const executeCode = async () => {
    setIsSubmitting(true); setTab("output");
    try {
      const res = await apiCall("/execute", { method: "POST", body: JSON.stringify({ user_id: user?.user_id, code }) });
      setOutput(res);
    } catch (e: any) { setOutput({ output: "Runtime Failed: " + e.message }); }
    finally { setIsSubmitting(false); }
  };

  return (
    <div className="w-full max-w-[1600px] mx-auto h-[900px] rounded-3xl border border-white/10 shadow-2xl relative bg-[#010103] text-zinc-300 flex flex-col overflow-hidden font-sans selection:bg-cyan-500/30">
      {/* Dynamic BG */}
      <div className="absolute inset-0 pointer-events-none opacity-[0.05]" style={{ backgroundImage: "radial-gradient(#ffffff 1px, transparent 1px)", backgroundSize: "32px 32px" }} />
      <div className="absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 w-[120vw] h-[120vh] bg-[radial-gradient(circle_at_center,rgba(6,182,212,0.05)_0%,transparent_70%)] pointer-events-none -z-10" />

      {/* Header - Slim & HighTech */}
      <header className="h-16 flex items-center justify-between px-8 border-b border-white/5 bg-black/50 backdrop-blur-xl z-50">
        <div className="flex items-center gap-6">
          <div className="flex items-center gap-2">
            <div className="w-8 h-8 rounded-lg bg-gradient-to-tr from-cyan-600 to-purple-600 flex items-center justify-center p-[2px]">
              <div className="w-full h-full bg-black rounded-[6px] flex items-center justify-center">
                <Workflow className="w-4 h-4 text-cyan-400" />
              </div>
            </div>
            <h1 className="text-lg font-black tracking-tighter uppercase italic">SandPy <span className="text-cyan-500">Node</span></h1>
          </div>

          <div className="h-8 w-[1px] bg-white/10" />

          <div className="flex gap-4">
            {WORKERS.map(w => (
              <div key={w.id} className="flex items-center gap-2 group cursor-pointer" onClick={() => setActiveWorker(w)}>
                <div className={`w-2 h-2 rounded-full ${activeWorker.id === w.id ? 'bg-cyan-500 animate-pulse shadow-[0_0_8px_#06b6d4]' : 'bg-zinc-800'}`} />
                <span className={`text-[10px] font-black uppercase tracking-widest ${activeWorker.id === w.id ? 'text-white' : 'text-zinc-600 group-hover:text-zinc-400'}`}>{w.display}</span>
              </div>
            ))}
          </div>
        </div>

        <div className="flex items-center gap-6">
          <div className="flex flex-col items-end">
            <span className="text-[9px] font-black uppercase text-zinc-600 tabular-nums">Environment Locked By</span>
            <span className={`text-[11px] font-bold ${lockOwner ? 'text-red-400' : 'text-cyan-400'}`}>{lockOwner || "UNLOCKED"}</span>
          </div>
          <div className="flex items-center gap-3 pl-6 border-l border-white/10">
            <div className="w-7 h-7 rounded-full bg-zinc-900 border border-white/5 flex items-center justify-center text-xs font-bold">{user?.user_id?.[0]}</div>
            <span className="text-xs font-black uppercase tracking-tighter text-white">{user?.user_id}</span>
            <button className="text-zinc-600 hover:text-white transition"><LogOut className="w-4 h-4" /></button>
          </div>
        </div>
      </header>

      {/* Main Grid - Zero Padding Layout */}
      <main className="flex-1 grid grid-cols-12 overflow-hidden">

        {/* Left Column - Core Interface */}
        <div className="col-span-8 flex flex-col border-r border-white/5 bg-[#030307] min-h-0 overflow-hidden">

          {/* Agent Visual Pipeline */}
          <div className="px-8 pt-8 pb-4">
            <Pipeline activeAgent={activeAgent} statuses={agentStatuses} />
          </div>

          {/* Monaco Area */}
          <div className="flex-1 flex flex-col px-8 pb-4 min-h-0">
            <Card3D className="flex-1 min-h-0 bg-black/60 rounded-3xl border border-white/5 overflow-hidden flex flex-col relative shadow-2xl">
              <div className="absolute top-4 right-4 z-50 flex gap-2">
                {activeAgent && (
                  <motion.div animate={{ scale: [1, 1.1, 1] }} transition={{ repeat: Infinity }} className="px-3 py-1 bg-cyan-500/20 border border-cyan-500/40 rounded-full flex items-center gap-2">
                    <Bot className="w-3 h-3 text-cyan-400" />
                    <span className="text-[10px] font-black uppercase text-cyan-400">{activeAgent} writing...</span>
                  </motion.div>
                )}
              </div>
              <div className="flex-1 overflow-hidden">
                <MonacoEditor
                  height="100%" defaultLanguage="python" theme="vs-dark" value={code}
                  onChange={v => setCode(v || "")}
                  options={{ minimap: { enabled: false }, fontSize: 13, smoothScrolling: true, padding: { top: 20 }, cursorBlinking: "smooth" }}
                />
              </div>
            </Card3D>
            <div className="flex-shrink-0 flex items-center justify-between px-4 py-3 bg-black/60 border border-white/5 rounded-2xl mt-3">
              <div className="flex gap-4">
                <button onClick={executeCode} disabled={isSubmitting} className="h-10 px-6 rounded-xl bg-cyan-600 hover:bg-cyan-500 text-white text-[11px] font-black uppercase tracking-widest transition flex items-center gap-2 group shadow-lg shadow-cyan-900/20 active:scale-95">
                  <Power className="w-4 h-4 group-hover:rotate-90 transition-transform" /> Execute Main
                </button>
                <button onClick={deployCode} disabled={isSubmitting} className="h-10 px-6 rounded-xl bg-purple-600 hover:bg-purple-500 text-white text-[11px] font-black uppercase tracking-widest transition flex items-center gap-2 group shadow-lg shadow-purple-900/20 active:scale-95">
                  <Globe className="w-4 h-4 group-hover:-translate-y-1 transition-transform" /> Auto Deploy
                </button>
                <button className="h-10 px-4 rounded-xl bg-zinc-900 hover:bg-zinc-800 text-zinc-400 transition flex items-center gap-2"><History className="w-4 h-4" /> 0x42F</button>
              </div>
              <div className="text-[10px] font-black uppercase text-zinc-600 tracking-wider">Kernal Status: Idle | Latency: 12ms</div>
            </div>
          </div>
        </div>

        {/* Right Column - Controls & Feedback */}
        <div className="col-span-4 flex flex-col bg-black/20 min-h-0 overflow-hidden">

          {/* Swarm Command Module */}
          <div className="p-8 border-b border-white/5">
            <div className="flex items-center gap-2 mb-4">
              <Settings2 className="w-4 h-4 text-purple-400" />
              <span className="text-[10px] font-black uppercase tracking-widest text-zinc-400">Swarm Configuration</span>
            </div>
            <div className="grid grid-cols-2 gap-2 mb-6">
              {AGENTS.map(agent => (
                <button
                  key={agent.id} onClick={() => setEnabledAgents(p => p.includes(agent.id) ? p.filter(a => a !== agent.id) : [...p, agent.id])}
                  className={`h-10 rounded-xl border flex items-center gap-3 px-4 transition-all ${enabledAgents.includes(agent.id) ? 'border-purple-500/50 bg-purple-500/10 text-white shadow-[inset_0_0_10px_rgba(168,85,247,0.1)]' : 'border-white/5 bg-white/5 text-zinc-500 opacity-50'}`}
                >
                  <agent.icon className="w-4 h-4" />
                  <span className="text-[10px] font-black uppercase tracking-tighter">{agent.name}</span>
                </button>
              ))}
            </div>

            <div className="flex items-center gap-2 mb-4">
              <Sparkles className="w-4 h-4 text-cyan-400" />
              <span className="text-[10px] font-black uppercase tracking-widest text-zinc-400">Intelligence Matrix</span>
            </div>
            <div className="grid grid-cols-2 gap-2 mb-6">
              {MODELS.map(m => (
                <button
                  key={m.id} onClick={() => setSelectedModel(m.id)}
                  className={`h-12 rounded-xl border flex flex-col items-start justify-center px-4 transition-all ${selectedModel === m.id ? 'border-cyan-500/50 bg-cyan-500/10 text-white shadow-[inset_0_0_10px_rgba(6,182,212,0.1)]' : 'border-white/5 bg-white/5 text-zinc-500 opacity-50'}`}
                >
                  <span className="text-[9px] font-black uppercase tracking-widest opacity-50">{m.provider}</span>
                  <span className="text-[10px] font-black uppercase tracking-tighter truncate w-full">{m.name}</span>
                </button>
              ))}
            </div>

            <div className="relative group">
              <div className="absolute inset-0 bg-cyan-500/20 blur opacity-0 group-focus-within:opacity-100 transition-opacity" />
              <textarea
                value={prompt} onChange={e => setPrompt(e.target.value)}
                className="relative w-full h-32 bg-zinc-950 border border-white/10 rounded-2xl p-4 text-xs resize-none focus:outline-none focus:border-cyan-500/50 transition-colors"
                placeholder="Enter objective for the swarm..."
              />
              <button onClick={startSwarm} className="absolute bottom-4 right-4 w-10 h-10 rounded-xl bg-cyan-600 flex items-center justify-center text-white hover:scale-110 active:scale-95 transition shadow-lg shadow-cyan-900/20"><Send className="w-4 h-4" /></button>
            </div>
          </div>

          {/* Multi-Tab Terminal Area */}
          <div className="flex-1 flex flex-col p-8 overflow-hidden">
            <div className="flex gap-4 mb-4 border-b border-white/5">
              <button onClick={() => setTab("process")} className={`pb-2 text-[10px] font-black uppercase tracking-widest transition-colors ${tab === "process" ? 'text-cyan-400 border-b-2 border-cyan-400' : 'text-zinc-600'}`}>Agent Stream</button>
              <button onClick={() => setTab("output")} className={`pb-2 text-[10px] font-black uppercase tracking-widest transition-colors ${tab === "output" ? 'text-purple-400 border-b-2 border-purple-400' : 'text-zinc-600'}`}>Runtime Stdout</button>
              <button onClick={() => setTab("services")} className={`pb-2 text-[10px] font-black uppercase tracking-widest transition-colors ${tab === "services" ? 'text-emerald-400 border-b-2 border-emerald-400' : 'text-zinc-600'}`}>BG Deploys</button>
            </div>

            <div className="flex-1 bg-black/60 rounded-3xl border border-white/5 p-6 overflow-y-auto space-y-3 thin-scroll">
              {tab === "process" ? (
                <AnimatePresence>
                  {swarmEvents.length === 0 ? (
                    <div className="h-full flex flex-col items-center justify-center opacity-20 mt-10">
                      <Activity className="w-12 h-12 mb-4" />
                      <p className="text-[10px] font-black uppercase">Awaiting Operational Stream</p>
                    </div>
                  ) : swarmEvents.map((ev, i) => (
                    <motion.div key={i} initial={{ opacity: 0, x: -10 }} animate={{ opacity: 1, x: 0 }} className="flex gap-3">
                      <span className="text-[9px] font-black uppercase py-0.5 px-1.5 rounded bg-white/5 text-zinc-500 h-fit">{ev.role[0]}</span>
                      <div className="flex-1">
                        <p className="text-[11px] leading-relaxed text-zinc-300">{ev.message}</p>
                        {ev.metadata?.output && <pre className="mt-2 p-2 bg-red-500/10 text-red-300 text-[10px] rounded border border-red-500/20 overflow-x-auto">{ev.metadata.output}</pre>}
                      </div>
                    </motion.div>
                  ))}
                </AnimatePresence>
              ) : tab === "output" ? (
                <div className="space-y-4">
                  {output?.output ? <pre className="text-[11px] font-mono text-emerald-300 bg-emerald-500/5 p-4 rounded-xl border border-emerald-500/20 whitespace-pre-wrap leading-relaxed">{output.output}</pre> : <p className="text-center py-20 text-[10px] font-black uppercase opacity-20">No Runtime Output Detected</p>}
                  {output?.images?.map((img: string, i: number) => (
                    <div key={i} className="relative group">
                      <img src={`data:image/png;base64,${img}`} className="w-full rounded-2xl border border-white/10 group-hover:scale-[1.02] transition-transform duration-500" />
                      <div className="absolute inset-0 bg-cyan-500/10 opacity-0 group-hover:opacity-100 transition-opacity rounded-2xl pointer-events-none" />
                    </div>
                  ))}
                </div>
              ) : (
                <div className="space-y-4">
                  {servicesList.length === 0 ? (
                    <div className="h-full flex flex-col items-center justify-center opacity-20 mt-10">
                      <Globe className="w-12 h-12 mb-4" />
                      <p className="text-center text-[10px] font-black uppercase">No Active BG Deployments</p>
                    </div>
                  ) : (
                    servicesList.map(svc => (
                      <div key={svc.service_id} className="p-4 bg-white/5 border border-white/10 rounded-xl flex items-center justify-between">
                         <div className="flex flex-col">
                            <span className="text-[12px] font-black tracking-widest uppercase flex items-center gap-2 text-emerald-400">
                               {svc.status === 'queued' ? <Loader2 className="w-4 h-4 animate-spin text-orange-400"/> : <Globe className="w-4 h-4"/>}
                               <span className={svc.status === 'queued' ? 'text-orange-400' : ''}>{svc.status}</span>
                            </span>
                            <span className="text-[10px] text-zinc-500 font-mono mt-2 flex items-center gap-2"><div className="w-1 h-1 bg-zinc-600 rounded-full"/> ID: {svc.service_id.slice(0,8)}</span>
                            <span className="text-[10px] text-zinc-500 font-mono mt-1 flex items-center gap-2"><div className="w-1 h-1 bg-zinc-600 rounded-full"/> Worker: <span className="text-cyan-400 ml-1">{svc.worker_id}</span></span>
                            <a href={`http://localhost:${svc.appPort}`} target="_blank" rel="noreferrer" className="text-[10px] text-cyan-400 font-mono mt-1 flex items-center gap-2 hover:underline">
                              <Globe className="w-3 h-3" /> localhost:{svc.appPort}
                            </a>
                         </div>
                         <button onClick={() => stopService(svc.service_id, svc.worker_id)} className="h-10 px-5 rounded-lg bg-red-500/10 hover:bg-red-500/20 text-red-500 border border-red-500/20 text-[10px] font-black uppercase transition shrink-0 tracking-widest flex items-center gap-2">
                           <Power className="w-3 h-3"/> Stop Process
                         </button>
                      </div>
                    ))
                  )}
                </div>
              )}
            </div>
          </div>
        </div>
      </main>

      <style jsx global>{`
        .thin-scroll::-webkit-scrollbar { width: 4px; }
        .thin-scroll::-webkit-scrollbar-track { background: transparent; }
        .thin-scroll::-webkit-scrollbar-thumb { background: rgba(255,255,255,0.05); border-radius: 10px; }
        .thin-scroll::-webkit-scrollbar-thumb:hover { background: rgba(6,182,212,0.2); }
      `}</style>
    </div>
  );
}
