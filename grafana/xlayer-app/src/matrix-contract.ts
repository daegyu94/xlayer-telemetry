import { Destination } from "./context";
// References to canonical panels. A rolling histogram/rate is deliberately
// contextual rather than presented as a short-phase KPI. No MFU is synthesized.
export const MATRIX_SPECS: Record<
  string,
  {
    dashboard: Destination;
    panel: number;
    refs: string[];
    unit: string;
    scope: string;
    rolling: boolean;
    label: string;
  }
> = {
  gpu: {
    dashboard: "compute",
    panel: 2,
    refs: ["A"],
    unit: "%",
    scope: "node",
    rolling: false,
    label: "GPU utilization",
  },
  vllm: {
    dashboard: "stage",
    panel: 9,
    refs: ["A"],
    unit: "requests",
    scope: "shared-service",
    rolling: false,
    label: "Waiting queue",
  },
  kv: {
    dashboard: "stage",
    panel: 28,
    refs: ["A"],
    unit: "ratio",
    scope: "shared-service",
    rolling: true,
    label: "Prefix token hit ratio",
  },
  ray: {
    dashboard: "stage",
    panel: 22,
    refs: ["A"],
    unit: "tasks",
    scope: "shared-service",
    rolling: false,
    label: "Task states (identity aggregated)",
  },
  network: {
    dashboard: "compute",
    panel: 9,
    refs: ["A"],
    unit: "B/s",
    scope: "node",
    rolling: true,
    label: "RDMA receive",
  },
  storage: {
    dashboard: "stage",
    panel: 60,
    refs: ["A"],
    unit: "s",
    scope: "shared-service",
    rolling: true,
    label: "KV connector RPC p95",
  },
  sandbox: {
    dashboard: "stage",
    panel: 12,
    refs: ["A"],
    unit: "ratio",
    scope: "worker/cgroup",
    rolling: false,
    label: "Sandbox I/O pressure",
  },
};
