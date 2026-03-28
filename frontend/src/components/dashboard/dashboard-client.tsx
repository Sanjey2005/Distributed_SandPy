"use client";

import React, { useState, useEffect, FormEvent } from "react";
import { Button } from "@/components/ui/flow-hover-button";
import { 
  Code2, Zap, Server, Clock, Eye, X, CheckCircle, AlertTriangle, FileCode, ServerOff,
  Search, Filter, ChevronLeft, ChevronRight, User, Cpu, Activity, RotateCcw, Play
} from "lucide-react";
import { Input } from "@/components/ui/input";
import { 
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue 
} from "@/components/ui/select";
import { Badge } from "@/components/ui/badge";

const BASE_URL = "http://localhost:8000";

const SAMPLE_PROGRAMS = [
  {
    name: "Fibonacci Sequence",
    code: `# Generate Fibonacci sequence up to n terms
def fibonacci(n):
    fib = [0, 1]
    for i in range(2, n):
        fib.append(fib[i-1] + fib[i-2])
    return fib[:n]

result = fibonacci(15)
print("Fibonacci sequence:", result)
print("Sum:", sum(result))`
  },
  {
    name: "Prime Numbers",
    code: `# Find all primes up to n
def is_prime(n):
    if n < 2:
        return False
    for i in range(2, int(n**0.5) + 1):
        if n % i == 0:
            return False
    return True

primes = [x for x in range(1, 51) if is_prime(x)]
print("Prime numbers 1-50:", primes)
print("Count:", len(primes))`
  },
  {
    name: "List Operations",
    code: `# Various list operations
numbers = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]

squares = [x**2 for x in numbers]
evens = [x for x in numbers if x % 2 == 0]

print("Original:", numbers)
print("Squares:", squares)
print("Evens:", evens)
print("Sum:", sum(numbers))
print("Max:", max(numbers))
print("Min:", min(numbers))`
  },
  {
    name: "Dictionary & JSON",
    code: `# Dictionary operations
student = {
    "name": "Alice",
    "age": 20,
    "courses": ["Math", "Physics", "CS"]
}

student["grade"] = "A"
student["gpa"] = 3.8

print("Student info:")
for key, value in student.items():
    print(f"  {key}: {value}")`
  },
  {
    name: "String Manipulation",
    code: `# String operations
text = "Hello, World! Python is amazing!"

print("Original:", text)
print("Upper:", text.upper())
print("Lower:", text.lower())
print("Words:", text.split())
print("Length:", len(text))
print("Replace:", text.replace("World", "Python"))`
  }
];

const PIPELINE_TEST_PROGRAMS = [
  {
    name: "Fibonacci Stress Test",
    userId: "pipeline-test-1",
    code: `import time
print("Starting Fibonacci computation...")
time.sleep(8)
def fibonacci(n):
    fib = [0, 1]
    for i in range(2, n):
        fib.append(fib[i-1] + fib[i-2])
    return fib[:n]
result = fibonacci(20)
print("Fibonacci result:", result)
print("Sum:", sum(result))`,
  },
  {
    name: "Prime Sieve",
    userId: "pipeline-test-2",
    code: `import time
print("Starting Prime sieve...")
time.sleep(10)
def sieve(n):
    is_prime = [True] * (n + 1)
    is_prime[0] = is_prime[1] = False
    for i in range(2, int(n**0.5) + 1):
        if is_prime[i]:
            for j in range(i*i, n+1, i):
                is_prime[j] = False
    return [x for x in range(n+1) if is_prime[x]]
primes = sieve(100)
print("Primes up to 100:", primes)
print("Count:", len(primes))`,
  },
  {
    name: "Matrix Operations",
    userId: "pipeline-test-3",
    code: `import time
print("Starting Matrix operations...")
time.sleep(12)
matrix = [[i*3+j+1 for j in range(3)] for i in range(3)]
print("Matrix:")
for row in matrix:
    print(row)
transpose = list(map(list, zip(*matrix)))
print("Transpose:")
for row in transpose:
    print(row)`,
  },
  {
    name: "Load Balance Proof",
    userId: "pipeline-test-4",
    code: `import time
print("Queue proof - this is the 4th job!")
print("All 3 workers were at capacity (1 job each).")
print("This job waited in queue until a worker freed up.")
time.sleep(15)
data = {"worker": "first-available", "proof": True}
for k, v in data.items():
    print(f"  {k}: {v}")
print("Capacity-limited scheduling verified!")`,
  },
];

export function DashboardClient() {
  const [workers, setWorkers] = useState<any[]>([]);
  const [jobs, setJobs] = useState<any[]>([]);
  const [connectionError, setConnectionError] = useState(false);
  
  const [userId, setUserId] = useState("");
  const [code, setCode] = useState("");
  const [sampleIndex, setSampleIndex] = useState(0);
  
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [submitMessage, setSubmitMessage] = useState<string | null>(null);
  const [submitError, setSubmitError] = useState<string | null>(null);
  
  const [modalOpen, setModalOpen] = useState(false);
  const [modalContent, setModalContent] = useState("");

  const [isPipelineRunning, setIsPipelineRunning] = useState(false);
  const [pipelineResults, setPipelineResults] = useState<Array<{
    name: string;
    userId: string;
    jobId: string;
    workerId: string;
    status: string;
  }>>([]);
  const [pipelineError, setPipelineError] = useState<string | null>(null);
  const [queueDepth, setQueueDepth] = useState(0);

  // Pagination & Filtering State
  const [currentPage, setCurrentPage] = useState(1);
  const [itemsPerPage] = useState(10);
  const [searchTerm, setSearchTerm] = useState("");
  const [statusFilter, setStatusFilter] = useState("all");
  const [workerFilter, setWorkerFilter] = useState("all");
  const [entityFilter, setEntityFilter] = useState("all");

  const fetchWorkers = async () => {
    try {
      const res = await fetch(`${BASE_URL}/workers`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      setWorkers(data.workers || []);
      setQueueDepth(data.queue_depth || 0);
      setConnectionError(false);
    } catch (e) {
      console.error('Failed to fetch workers:', e);
      setConnectionError(true);
    }
  };

  const fetchJobs = async () => {
    try {
      const res = await fetch(`${BASE_URL}/jobs`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      setJobs(data.jobs || []);
      setConnectionError(false);
    } catch (e) {
      console.error('Failed to fetch jobs:', e);
      setConnectionError(true);
    }
  };

  useEffect(() => {
    fetchWorkers();
    fetchJobs();
    const workersInterval = setInterval(fetchWorkers, 3000);
    const jobsInterval = setInterval(fetchJobs, 2000);
    return () => {
      clearInterval(workersInterval);
      clearInterval(jobsInterval);
    };
  }, []);

  const loadSampleProgram = (e: React.MouseEvent) => {
    e.preventDefault();
    setCode(SAMPLE_PROGRAMS[sampleIndex].code);
    setSampleIndex((sampleIndex + 1) % SAMPLE_PROGRAMS.length);
  };

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault();
    setIsSubmitting(true);
    setSubmitError(null);
    setSubmitMessage(null);

    try {
      const res = await fetch(`${BASE_URL}/submit`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ user_id: userId, code }),
      });

      if (!res.ok) throw new Error(`Dispatch Failure [HTTP ${res.status}]`);

      const data = await res.json();
      setSubmitMessage(`Job: ${data.job_id} | ${data.status === 'queued' ? 'Status: QUEUED' : `Worker: ${data.worker_id}`}`);
      setCode("");
      setTimeout(() => setSubmitMessage(null), 8000);
    } catch (e: any) {
      console.error('Failed to submit job:', e);
      setSubmitError('SUBMISSION TERMINATED: ' + e.message);
      setTimeout(() => setSubmitError(null), 5000);
    } finally {
      setIsSubmitting(false);
    }
  };

  const fetchOutput = async (jobId: string) => {
    try {
      const res = await fetch(`${BASE_URL}/job/${jobId}`);
      if (!res.ok) throw new Error("Failed to fetch execution context.");
      const job = await res.json();
      setModalContent(job.output || '(No output recorded)');
      setModalOpen(true);
    } catch (e: any) {
      setModalContent('Network parity error: ' + e.message);
      setModalOpen(true);
    }
  };

  const handleTestPipeline = async () => {
    setIsPipelineRunning(true);
    setPipelineResults([]);
    setPipelineError(null);

    try {
      const promises = PIPELINE_TEST_PROGRAMS.map(async (program) => {
        const res = await fetch(`${BASE_URL}/submit`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ user_id: program.userId, code: program.code }),
        });

        if (!res.ok) throw new Error(`Failed for ${program.name}: HTTP ${res.status}`);

        const data = await res.json();
        return {
          name: program.name,
          userId: program.userId,
          jobId: data.job_id,
          workerId: data.worker_id,
          status: data.status || "pending",
        };
      });

      const results = await Promise.all(promises);
      setPipelineResults(results);
      setTimeout(() => setPipelineResults([]), 30000);
    } catch (e: any) {
      console.error('Pipeline test failed:', e);
      setPipelineError('Pipeline test failed: ' + e.message);
      setTimeout(() => setPipelineError(null), 8000);
    } finally {
      setIsPipelineRunning(false);
    }
  };

  const handleResetCounts = async () => {
    try {
      const res = await fetch(`${BASE_URL}/reset-counts`, { method: 'POST' });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      setPipelineResults([]);
      await fetchWorkers();
    } catch (e: any) {
      console.error('Failed to reset counts:', e);
      setPipelineError('Reset failed: ' + e.message + ' — did you rebuild the dispatcher container?');
      setTimeout(() => setPipelineError(null), 8000);
    }
  };

  const totalJobs = workers.reduce((sum, w) => sum + (w.job_count || 0), 0);
  const colors = ['#06b6d4', '#10b981', '#f59e0b', '#ef4444', '#8b5cf6', '#ec4899'];

  // Derived State for Table Filters
  const uniqueEntities: string[] = Array.from(new Set(jobs.map(j => j.user_id).filter((id): id is string => typeof id === "string")));
  const uniqueWorkers: string[] = Array.from(new Set(jobs.map(j => j.worker_id).filter((id): id is string => typeof id === "string")));

  const filteredJobs = jobs.filter(j => {
    const matchesSearch = (j.user_id || "").toLowerCase().includes(searchTerm.toLowerCase()) || 
                          (j.job_id || "").toLowerCase().includes(searchTerm.toLowerCase());
    const matchesStatus = statusFilter === "all" || j.status === statusFilter;
    const matchesWorker = workerFilter === "all" || j.worker_id === workerFilter;
    const matchesEntity = entityFilter === "all" || j.user_id === entityFilter;
    
    return matchesSearch && matchesStatus && matchesWorker && matchesEntity;
  });

  const totalPages = Math.ceil(filteredJobs.length / itemsPerPage);
  const paginatedJobs = filteredJobs.slice((currentPage - 1) * itemsPerPage, currentPage * itemsPerPage);

  const handlePageChange = (newPage: number) => {
    if (newPage >= 1 && newPage <= totalPages) {
      setCurrentPage(newPage);
    }
  };

  const resetFilters = () => {
    setSearchTerm("");
    setStatusFilter("all");
    setWorkerFilter("all");
    setEntityFilter("all");
    setCurrentPage(1);
  };

  return (
    <div className="container mx-auto px-4 py-12 max-w-7xl relative z-10 selection:bg-cyan-500/30">
      
      {/* Background Glow */}
      <div className="fixed top-0 left-1/2 -translate-x-1/2 w-full h-[500px] bg-cyan-500/5 blur-[120px] pointer-events-none -z-10"></div>
      
      {/* Error Banner */}
      {connectionError && (
        <div className="glass-card overflow-hidden bg-red-950/20 border-red-500/20 mb-8 animate-in fade-in slide-in-from-top-4 duration-500">
          <div className="flex items-center gap-3 px-6 py-4">
            <div className="w-2 h-2 rounded-full bg-red-500 animate-pulse"></div>
            <p className="text-sm font-medium text-red-400">
              Connectivity Issue: Unable to reach dispatcher at <span className="font-mono">{BASE_URL}</span>
            </p>
            <button onClick={() => { fetchWorkers(); fetchJobs(); }} className="ml-auto text-xs font-semibold uppercase tracking-wider text-red-400 hover:text-red-300 transition underline underline-offset-4">
              Retry Now
            </button>
          </div>
        </div>
      )}



      <div className="grid grid-cols-1 lg:grid-cols-12 gap-8 mb-12">
        {/* IDE Section */}
        <div className="lg:col-span-8 flex flex-col gap-6">
          <section className="glass-card p-8 flex-1 flex flex-col">
            <div className="flex items-center justify-between mb-6">
              <div className="flex items-center gap-3">
                <div className="p-2 bg-cyan-500/10 rounded-lg text-cyan-400">
                  <Code2 className="w-5 h-5" />
                </div>
                <h2 className="text-xl font-semibold text-zinc-100">Code Workspace</h2>
              </div>
              <div className="flex items-center gap-3">
                <Button 
                  icon={<FileCode className="w-4 h-4" />} 
                  onClick={loadSampleProgram}
                >
                  Load Sample
                </Button>
                <div className="text-xs text-zinc-500 font-mono">Python 3.10+ Executable</div>
              </div>
            </div>
            
            <form onSubmit={handleSubmit} className="space-y-6 flex-1 flex flex-col">
              <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
                <div className="space-y-2">
                  <label className="block text-xs font-bold uppercase tracking-wider text-zinc-500 ml-1">User Identifier</label>
                  <input type="text" value={userId} onChange={e => setUserId(e.target.value)} required 
                      className="w-full px-4 py-3 rounded-lg input-field transition text-sm font-medium text-zinc-100"
                      placeholder="Enter your system ID" />
                </div>
              </div>
              
              <div className="space-y-2 flex-1 flex flex-col">
                <label className="block text-xs font-bold uppercase tracking-wider text-zinc-500 ml-1">Logic Payload</label>
                <div className="relative flex-1 min-h-[300px] flex flex-col">
                  <textarea value={code} onChange={e => setCode(e.target.value)} required
                      className="w-full flex-1 px-5 py-4 rounded-xl input-field font-mono text-sm leading-relaxed resize-none text-zinc-100 placeholder:text-zinc-700"
                      placeholder="# Enter your Python code here...&#10;# (Note: Interactive inputs are not supported)"></textarea>
                  <div className="absolute bottom-4 right-4 flex gap-2">
                    <span className="px-2 py-1 bg-zinc-950/50 border border-white/5 rounded text-[10px] font-mono text-zinc-500">UTF-8</span>
                  </div>
                </div>
              </div>

              <div className="pt-4 mt-auto">
                <Button 
                  type="submit" 
                  disabled={isSubmitting}
                  icon={isSubmitting ? (
                    <div className="animate-spin rounded-full h-5 w-5 border-b-2 border-zinc-800 dark:border-white"></div>
                  ) : <Zap className="w-5 h-5 fill-current" />}
                  className="w-full"
                >
                  {isSubmitting ? "Syncing Cluster..." : "Execute Code Sequence"}
                </Button>
              </div>
            </form>

            {submitMessage && (
              <div className="mt-6 animate-in zoom-in-95 duration-300">
                <div className="bg-emerald-500/5 border border-emerald-500/20 rounded-xl p-5">
                  <div className="flex items-start gap-4">
                    <div className="p-2 bg-emerald-500/20 rounded-lg text-emerald-400 mt-1">
                      <CheckCircle className="w-5 h-5" />
                    </div>
                    <div>
                      <p className="text-sm font-semibold text-emerald-400 mb-1">Job Dispatched Successfully</p>
                      <div className="text-xs font-mono text-zinc-300 uppercase tracking-widest">{submitMessage}</div>
                    </div>
                  </div>
                </div>
              </div>
            )}
            
            {submitError && (
              <div className="mt-6 bg-red-500/5 border border-red-500/20 text-red-400 px-5 py-4 rounded-xl text-sm animate-in shake duration-300">
                {submitError}
              </div>
            )}
          </section>
        </div>

        {/* Worker List with Load Distribution */}
        <div className="lg:col-span-4">
          <section className="glass-card p-8 h-full">
            <div className="flex items-center gap-3 mb-6">
              <div className="p-2 bg-emerald-500/10 rounded-lg text-emerald-400">
                <Server className="w-5 h-5" />
              </div>
              <h2 className="text-xl font-semibold text-zinc-100">Active Workers</h2>
            </div>
            
            <div className="mb-6 p-4 rounded-xl bg-zinc-900/50 border border-white/5">
              <div className="flex items-center justify-between mb-3">
                <span className="text-[10px] font-bold uppercase tracking-widest text-zinc-500">Workload Distribution</span>
                <span className="text-[10px] font-mono text-zinc-600">{totalJobs} total</span>
              </div>
              
              <div className="h-4 rounded-full overflow-hidden flex bg-zinc-800">
                {totalJobs === 0 ? (
                  <div className="w-full h-full bg-zinc-700 rounded-full"></div>
                ) : (
                  workers.map((w, i) => w.job_count > 0 && (
                    <div key={w.worker_id} className="worker-bar h-full" style={{ width: `${(w.job_count / totalJobs) * 100}%`, backgroundColor: colors[i % colors.length] }} title={`${w.worker_id}: ${w.job_count} jobs`}></div>
                  ))
                )}
              </div>
              <div className="mt-3 flex flex-wrap gap-2">
                {totalJobs === 0 ? (
                  <span className="text-[10px] text-zinc-500">No jobs yet</span>
                ) : (
                  workers.map((w, i) => w.job_count > 0 && (
                    <div key={w.worker_id + 'legend'} className="flex items-center gap-1 text-[10px]">
                      <span className="w-2 h-2 rounded-full" style={{ backgroundColor: colors[i % colors.length] }}></span>
                      <span className="text-zinc-500">{w.worker_id}: {w.job_count}</span>
                    </div>
                  ))
                )}
              </div>
            </div>

            {queueDepth > 0 && (
              <div className="mb-6 p-3 rounded-xl bg-amber-500/5 border border-amber-500/20">
                <div className="flex items-center gap-2">
                  <div className="w-2 h-2 rounded-full bg-amber-500 animate-pulse"></div>
                  <span className="text-[10px] font-bold uppercase tracking-widest text-amber-400">
                    {queueDepth} job{queueDepth > 1 ? 's' : ''} queued
                  </span>
                </div>
              </div>
            )}

            <div className="space-y-4">
              {workers.length === 0 && !connectionError ? (
                <div className="flex justify-center py-12">
                  <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-emerald-500"></div>
                </div>
              ) : workers.length === 0 && connectionError ? (
                <div className="flex flex-col items-center gap-4 py-8 opacity-50 grayscale">
                    <ServerOff className="w-12 h-12 text-zinc-500" />
                    <p className="text-sm font-bold uppercase tracking-widest text-zinc-500">Dispatcher Link Broken</p>
                </div>
              ) : (
                workers.map(w => (
                  <div key={w.worker_id} className="p-4 rounded-xl border border-white/5 bg-white/[0.02] flex items-center justify-between hover:bg-white/[0.04] transition group min-w-0">
                    <div className="flex items-center gap-4 min-w-0">
                      <div className={`p-2 rounded-lg transition shrink-0 ${w.healthy ? 'bg-emerald-500/10 text-emerald-500' : 'bg-red-500/10 text-red-500'}`}>
                        {w.healthy ? <Server className="w-4 h-4" /> : <AlertTriangle className="w-4 h-4" />}
                      </div>
                      <div className="min-w-0">
                        <h4 className="text-sm font-bold tracking-tight text-zinc-200 truncate">{w.worker_id}</h4>
                        <p className="text-[10px] font-mono text-zinc-500 uppercase tracking-tighter mt-0.5 truncate">{w.url}</p>
                      </div>
                    </div>
                    <div className="flex items-center gap-4 shrink-0">
                      <div className="text-right hidden sm:block">
                        <span className="block text-[10px] font-black tracking-widest text-zinc-600 uppercase mb-0.5">Payloads</span>
                        <span className="text-xs font-mono font-bold text-zinc-400">{w.job_count || 0}</span>
                      </div>
                      <Badge variant="outline" className={`text-[10px] font-bold tracking-tight px-3 py-1 ${w.healthy ? 'bg-emerald-500/10 text-emerald-400 border-emerald-500/20' : 'bg-red-500/10 text-red-400 border-red-500/20'}`}>
                        {w.healthy ? 'ONLINE' : 'OFFLINE'}
                      </Badge>
                    </div>
                  </div>
                ))
              )}
            </div>
          </section>
        </div>
      </div>

      {/* Test Pipeline Section */}
      <section className="glass-card p-8 mb-12">
        <div className="flex items-center justify-between mb-6">
          <div className="flex items-center gap-3">
            <div className="p-2 bg-purple-500/10 rounded-lg text-purple-400">
              <Play className="w-5 h-5" />
            </div>
            <div>
              <h2 className="text-xl font-semibold text-zinc-100">Test Pipeline</h2>
              <p className="text-xs text-zinc-500 mt-1">
                Submit 4 programs simultaneously to demonstrate load balancing across all workers
              </p>
            </div>
          </div>
          <div className="flex items-center gap-3">
            <Button
              icon={<RotateCcw className="w-4 h-4" />}
              onClick={handleResetCounts}
            >
              Reset Counters
            </Button>
            <Button
              icon={isPipelineRunning ? (
                <div className="animate-spin rounded-full h-5 w-5 border-b-2 border-zinc-800 dark:border-white"></div>
              ) : <Zap className="w-5 h-5 fill-current" />}
              onClick={handleTestPipeline}
              disabled={isPipelineRunning}
              className="px-6"
            >
              {isPipelineRunning ? "Dispatching..." : "Run Test Pipeline"}
            </Button>
          </div>
        </div>

        {pipelineResults.length > 0 && (
          <div className="animate-in fade-in slide-in-from-bottom-4 duration-500">
            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
              {pipelineResults.map((result, i) => (
                <div key={result.jobId} className="p-4 rounded-xl border border-white/5 bg-white/[0.02]">
                  <div className="flex items-center gap-2 mb-2">
                    <span className="w-2 h-2 rounded-full" style={{ backgroundColor: colors[i % colors.length] }}></span>
                    <span className="text-xs font-bold text-zinc-300">{result.name}</span>
                  </div>
                  <div className="space-y-1 text-[10px] font-mono text-zinc-500">
                    <div>USER: <span className="text-zinc-400">{result.userId}</span></div>
                    <div>WORKER: <span className={`font-bold ${result.status === 'queued' ? 'text-amber-400' : 'text-cyan-400'}`}>{result.workerId === 'queued' ? 'WAITING IN QUEUE' : result.workerId}</span></div>
                    <div>JOB: <span className="text-zinc-400">{result.jobId.substring(0, 8)}</span></div>
                  </div>
                </div>
              ))}
            </div>
            <div className="mt-4 p-3 rounded-lg bg-emerald-500/5 border border-emerald-500/20">
              <p className="text-xs text-emerald-400">
                Pipeline dispatched. Watch the workload distribution bar above to see real-time load balancing.
                Jobs 1-3 each went to a different worker. Job 4 is queued until a worker becomes free, proving capacity-limited scheduling.
              </p>
            </div>
          </div>
        )}

        {pipelineError && (
          <div className="mt-4 bg-red-500/5 border border-red-500/20 text-red-400 px-5 py-4 rounded-xl text-sm animate-in shake duration-300">
            {pipelineError}
          </div>
        )}
      </section>

      {/* Jobs History table */}
      <section className="glass-card p-1">
        <div className="px-8 pt-8 pb-4 space-y-4">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-3">
              <div className="p-2 bg-amber-500/10 rounded-lg text-amber-500">
                <Clock className="w-5 h-5" />
              </div>
              <h2 className="text-xl font-semibold text-zinc-200">Execution History</h2>
            </div>
            
            {(statusFilter !== "all" || workerFilter !== "all" || entityFilter !== "all" || searchTerm !== "") && (
              <button 
                onClick={resetFilters}
                className="text-[10px] font-bold uppercase tracking-widest text-zinc-500 hover:text-cyan-400 transition-colors"
              >
                Clear all filters
              </button>
            )}
          </div>

          <div className="flex flex-wrap items-center gap-3">
            <div className="relative w-full sm:w-60">
              <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-zinc-500 z-10" />
              <Input 
                placeholder="Search Request ID..." 
                value={searchTerm}
                onChange={(e) => { setSearchTerm(e.target.value); setCurrentPage(1); }}
                className="pl-9 bg-zinc-900/40 border-white/10 h-10 text-xs w-full"
              />
            </div>
            
            <Select value={statusFilter} onValueChange={(val) => { if (val) setStatusFilter(val); setCurrentPage(1); }}>
              <SelectTrigger className="bg-zinc-900/40 border-white/10 h-10 text-xs text-zinc-300 w-full sm:w-[130px]">
                <div className="flex items-center gap-2">
                  <Activity className="w-4 h-4 text-zinc-500" />
                  <SelectValue placeholder="Status" />
                </div>
              </SelectTrigger>
              <SelectContent className="bg-zinc-950 border-white/10 text-zinc-300">
                <SelectItem value="all">States</SelectItem>
                <SelectItem value="completed">Completed</SelectItem>
                <SelectItem value="running">Running</SelectItem>
                <SelectItem value="failed">Failed</SelectItem>
                <SelectItem value="queued">Queued</SelectItem>
                <SelectItem value="pending">Pending</SelectItem>
              </SelectContent>
            </Select>

            <Select value={entityFilter} onValueChange={(val) => { if (val) setEntityFilter(val); setCurrentPage(1); }}>
              <SelectTrigger className="bg-zinc-900/40 border-white/10 h-10 text-xs text-zinc-300 w-full sm:w-[130px]">
                <div className="flex items-center gap-2">
                  <User className="w-4 h-4 text-zinc-500" />
                  <SelectValue placeholder="Entity" />
                </div>
              </SelectTrigger>
              <SelectContent className="bg-zinc-950 border-white/10 text-zinc-300">
                <SelectItem value="all">Entities</SelectItem>
                {uniqueEntities.map(id => (
                  <SelectItem key={id} value={id}>{id}</SelectItem>
                ))}
              </SelectContent>
            </Select>

            <Select value={workerFilter} onValueChange={(val) => { if (val) setWorkerFilter(val); setCurrentPage(1); }}>
              <SelectTrigger className="bg-zinc-900/40 border-white/10 h-10 text-xs text-zinc-300 w-full sm:w-[130px]">
                <div className="flex items-center gap-2">
                  <Cpu className="w-4 h-4 text-zinc-500" />
                  <SelectValue placeholder="Worker" />
                </div>
              </SelectTrigger>
              <SelectContent className="bg-zinc-950 border-white/10 text-zinc-300">
                <SelectItem value="all">Workers</SelectItem>
                {uniqueWorkers.map(id => (
                  <SelectItem key={id} value={id}>{id}</SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
        </div>
        
        <div className="overflow-x-auto">
          <table className="w-full text-left">
            <thead>
              <tr className="text-zinc-500 text-[10px] uppercase font-black tracking-[0.15em] border-b border-white/5">
                <th className="px-8 py-5">Request ID</th>
                <th className="px-4 py-5">Entity</th>
                <th className="px-4 py-5">Node</th>
                <th className="px-4 py-5">State</th>
                <th className="px-4 py-5">Manifest</th>
                <th className="px-8 py-5 text-right">Timestamp</th>
              </tr>
            </thead>
            <tbody className="text-sm text-zinc-300">
              {paginatedJobs.length === 0 ? (
                <tr>
                  <td colSpan={6} className="py-20 text-center text-zinc-600 uppercase tracking-[0.2em] font-bold text-xs">
                    {connectionError ? 'Connection Error' : searchTerm || statusFilter !== 'all' ? 'No matching records' : 'Awaiting First Execution'}
                  </td>
                </tr>
              ) : (
                paginatedJobs.map(j => {
                  let statusBadgeContent = null;
                  if (j.status === 'completed') statusBadgeContent = <Badge variant="outline" className="bg-emerald-500/10 text-emerald-400 border-emerald-500/20 text-[9px] font-bold tracking-tight px-2 py-0">COMPLETED</Badge>;
                  else if (j.status === 'running') statusBadgeContent = <Badge variant="outline" className="bg-cyan-500/10 text-cyan-400 border-cyan-500/20 text-[9px] font-bold tracking-tight px-2 py-0 animate-pulse">RUNNING</Badge>;
                  else if (j.status === 'failed') statusBadgeContent = <Badge variant="outline" className="bg-red-500/10 text-red-400 border-red-500/20 text-[9px] font-bold tracking-tight px-2 py-0">FAILED</Badge>;
                  else if (j.status === 'queued') statusBadgeContent = <Badge variant="outline" className="bg-amber-500/10 text-amber-400 border-amber-500/20 text-[9px] font-bold tracking-tight px-2 py-0 animate-pulse">QUEUED</Badge>;
                  else statusBadgeContent = <Badge variant="outline" className="bg-zinc-500/10 text-zinc-400 border-zinc-500/20 text-[9px] font-bold tracking-tight px-2 py-0">PENDING</Badge>;

                  return (
                    <tr key={j.job_id} className="border-b border-white/[0.02] hover:bg-white/[0.02] transition-colors group">
                      <td className="px-8 py-4 font-mono text-xs text-zinc-500 group-hover:text-cyan-400 transition-colors uppercase">{(j.job_id || '').substring(0, 12)}</td>
                      <td className="px-4 py-4 text-xs font-semibold capitalize">{j.user_id || '-'}</td>
                      <td className="px-4 py-4 text-xs font-mono text-zinc-500 uppercase">{j.worker_id || '-'}</td>
                      <td className="px-4 py-4 text-[10px]">
                        {statusBadgeContent}
                      </td>
                      <td className="px-4 py-4">
                        <button onClick={() => fetchOutput(j.job_id)} 
                            className="text-[10px] font-black uppercase tracking-widest text-cyan-500 hover:text-cyan-300 transition-colors flex items-center gap-2">
                            <Eye className="w-3 h-3" />
                            Inspect
                        </button>
                      </td>
                      <td className="px-8 py-4 text-right text-[10px] font-mono text-zinc-500 uppercase tracking-tighter font-bold">
                        {j.timestamp ? new Date(j.timestamp * 1000).toLocaleTimeString([], { hour12: false }) : '-'}
                      </td>
                    </tr>
                  )
                })
              )}
            </tbody>
          </table>
        </div>

        {/* Pagination Footer */}
        {totalPages > 1 && (
          <div className="px-8 py-4 border-t border-white/5 flex items-center justify-between bg-white/[0.01]">
            <span className="text-[10px] font-bold text-zinc-500 uppercase tracking-widest">
              Showing {(currentPage - 1) * itemsPerPage + 1} - {Math.min(currentPage * itemsPerPage, filteredJobs.length)} of {filteredJobs.length} records
            </span>
            <div className="flex items-center gap-2">
              <button 
                onClick={() => handlePageChange(currentPage - 1)}
                disabled={currentPage === 1}
                className="p-1.5 rounded-lg border border-white/5 bg-zinc-900/50 text-zinc-500 disabled:opacity-30 hover:text-white transition-colors"
              >
                <ChevronLeft className="w-4 h-4" />
              </button>
              
              <div className="flex items-center gap-1">
                {[...Array(totalPages)].map((_, i) => (
                  <button
                    key={i + 1}
                    onClick={() => handlePageChange(i + 1)}
                    className={`w-7 h-7 rounded-lg text-[10px] font-bold transition-all ${currentPage === i + 1 ? 'bg-cyan-500/20 text-cyan-400 border border-cyan-500/30' : 'text-zinc-500 hover:text-zinc-300'}`}
                  >
                    {i + 1}
                  </button>
                ))}
              </div>

              <button 
                onClick={() => handlePageChange(currentPage + 1)}
                disabled={currentPage === totalPages}
                className="p-1.5 rounded-lg border border-white/5 bg-zinc-900/50 text-zinc-500 disabled:opacity-30 hover:text-white transition-colors"
              >
                <ChevronRight className="w-4 h-4" />
              </button>
            </div>
          </div>
        )}
      </section>

      {/* Output Modal */}
      {modalOpen && (
        <div className="fixed inset-0 bg-zinc-950/80 backdrop-blur-md flex items-center justify-center z-50 p-4 transition-all duration-300" onClick={() => setModalOpen(false)}>
          <div className="glass-card max-w-3xl w-full max-h-[90vh] flex flex-col overflow-hidden shadow-2xl scale-100 opacity-100 transition-all duration-300" onClick={e => e.stopPropagation()}>
            <div className="flex justify-between items-center px-8 py-6 border-b border-white/5 bg-zinc-900/40">
              <div className="flex items-center gap-3">
                <div className="w-3 h-3 rounded-full bg-amber-500"></div>
                <div className="w-3 h-3 rounded-full bg-emerald-500"></div>
                <div className="w-3 h-3 rounded-full bg-cyan-500"></div>
                <h3 className="text-sm font-bold uppercase tracking-widest text-zinc-400 ml-2">Job Context Manifest</h3>
              </div>
              <button onClick={() => setModalOpen(false)} className="text-zinc-500 hover:text-white transition p-2 hover:bg-white/5 rounded-full">
                <X className="w-5 h-5" />
              </button>
            </div>
            <div className="flex-1 overflow-auto bg-black/40 p-8">
              <div className="mb-4 flex items-center gap-2 text-[10px] font-bold text-zinc-600 uppercase tracking-widest">
                <span className="w-2 h-2 rounded-full bg-cyan-500 animate-pulse"></span>
                Output Stream
              </div>
              <pre className="font-mono text-sm leading-relaxed text-zinc-300 whitespace-pre-wrap">{modalContent}</pre>
            </div>
            <div className="px-8 py-5 flex justify-end bg-zinc-900/40 border-t border-white/5">
              <Button onClick={() => setModalOpen(false)}>
                Dismiss
              </Button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
