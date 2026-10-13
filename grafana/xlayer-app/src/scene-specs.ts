import { Destination } from './context';

export type KpiSpec = {
  name: string;
  key?: string;
  source?: Destination;
  id?: number;
  refs?: string[];
  unit: string;
  description: string;
  phase?: string;
  scale?: number;
};

export const KPI_SPECS: KpiSpec[] = [
  {
    name: "Reward",
    source: "stage",
    id: 2,
    refs: ["A"],
    unit: "",
    description: "Run / Worker sample입니다. 아래 freshness 상태를 확인하세요.",
  },
  {
    name: "Step time",
    key: "step_duration_seconds",
    source: "overview",
    id: 30,
    unit: "s",
    description: "Run / Worker에서 완료된 observation입니다.",
  },
  {
    name: "Worker throughput",
    source: "overview",
    id: 31,
    unit: "tok/s",
    description: "SDK가 기록한 Worker sample입니다.",
  },
  {
    name: "Reported rollout",
    source: "stage",
    id: 4,
    refs: ["A"],
    phase: "rollout",
    unit: "s",
    description: "완료된 stage의 Reported duration입니다. 실제 execution boundary가 아닙니다.",
  },
  {
    name: "GPU utilization",
    key: "gpu_utilization_percent",
    source: "overview",
    id: 33,
    unit: "%",
    description: "관측한 device 중 utilization이 가장 높은 값입니다. Node scope이며 Run별 사용률이 아닙니다.",
  },
  {
    name: "KV token hit",
    source: "stage",
    id: 28,
    refs: ["A"],
    scale: 100,
    unit: "%",
    description: "Local prefix TOKEN hit입니다. Rolling / Shared engine 관측이며 Mooncake DFS hit와 다릅니다.",
  },
  {
    name: "3FS latency",
    key: "threefs_p99_latency",
    unit: "ms",
    description: "Entity별 reported p99의 최댓값입니다. Shared-service evidence이며 전체 요청의 p99, RPC p95 또는 disk mean이 아닙니다.",
  },
];

export const PRESSURE_SPECS: {
  name: string;
  signal?: string;
  dashboard: Destination;
  panel: number;
  refs: string[];
  unit: string;
  scope: string;
  scale?: number;
}[] = [
  {
    name: "vLLM waiting",
    signal: "vllm_requests_waiting",
    dashboard: "stage",
    panel: 9,
    refs: ["A"],
    unit: "requests",
    scope: "Shared engine의 queue를 Sampled로 관측한 값입니다.",
  },
  {
    name: "Ray task states",
    dashboard: "stage",
    panel: 22,
    refs: ["A"],
    unit: "tasks",
    scope: "Session 단위 집계입니다. Node identity는 제공되지 않습니다.",
  },
  {
    name: "Sandbox I/O PSI",
    signal: "sandbox_io_pressure_ratio",
    dashboard: "stage",
    panel: 12,
    refs: ["A"],
    unit: "%",
    scale: 100,
    scope: "Worker/cgroup sample입니다. Phase별 사용량으로 귀속하지 않습니다.",
  },
  {
    name: "RDMA tx wait",
    signal: "rdma_transmit_wait_ticks_per_second",
    dashboard: "compute",
    panel: 43,
    refs: ["A"],
    unit: "ticks/s",
    scope: "Port counter rate입니다. Latency 단위가 아닙니다.",
  },
  {
    name: "Storage busy",
    signal: "storage_device_busy_ratio",
    dashboard: "storage",
    panel: 3,
    refs: ["A"],
    unit: "%",
    scale: 100,
    scope: "Node/device의 Rolling busy ratio입니다.",
  },
  {
    name: "GPU utilization",
    signal: "gpu_utilization_percent",
    dashboard: "compute",
    panel: 2,
    refs: ["A"],
    unit: "%",
    scope: "Node/device의 Sampled 관측값입니다.",
  },
];
