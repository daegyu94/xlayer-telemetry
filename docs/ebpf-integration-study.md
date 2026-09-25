# eBPF Integration Feasibility Study

2026-09-26 기준 XLayer Telemetry main의 코드와 공식 도구 문서를 검토한 설계 조사입니다.
결론은 **단계적 hybrid**입니다.
현재 XLayer의 기본 경로에는 eBPF를 추가하지 않고, 먼저 기존 process metric과 workload identity의 연결을 검증합니다.
그 뒤에도 file I/O 지연 원인이나 scheduler wait가 빠져 실제 진단이 막히는 사례가 있으면 기존 eBPF 도구로 한 구간을 targeted profiling합니다.
DeepFlow와 Beyla는 서로 다른 조건에서 선택하는 optional provider이며, XLayer가 자체 eBPF platform을 만드는 근거는 현재 없습니다.

## 1. Why Consider eBPF

XLayer는 run과 step의 의미를 application에서 받고 GPU·host·vLLM·Ray·3FS 신호를 같은 시간 창에서 비교합니다.
그러나 Node Exporter의 NIC·disk 수치는 node 또는 device 전체이고, 3FS ClickHouse latency는 shared service의 관측치입니다.
이 값이 겹친다는 사실은 특정 VERL worker가 해당 traffic이나 I/O를 만들었다는 증거가 아닙니다.
eBPF는 process·flow·kernel wait에 관한 추가 증거를 줄 수 있지만 application의 rollout·reward·weight sync 의미를 복원하지는 못합니다.

~~~text
VERL / application semantics ----------------------+
Prometheus / 3FS measurements ---------------------+--> XLayer correlation
Logs ----------------------------------------------+    + diagnosis
Optional eBPF evidence ----------------------------+         |
                                                             +--> Grafana investigation
~~~

## 2. Current XLayer Audit

다음 표의 “구현”은 코드 경로가 있다는 뜻이고, 실제 값은 해당 exporter·endpoint·diagnostics 설정이 켜져 있어야 나옵니다.
XLayer 자체가 모든 source를 설치하거나 실행한다는 뜻은 아닙니다.

| 영역 | 현재 구현과 근거 | 남는 범위 |
| --- | --- | --- |
| Application SDK, VERL | application snapshot, VERL file logger bridge, 완료 step·stage 변환, wrapper ([application-metrics.md](application-metrics.md), [architecture.md](architecture.md), [verl.py](../xlayer_telemetry/adapters/verl.py)) | VERL의 현재 진행 phase와 정확한 stage start/end는 자동 계측하지 않음 |
| Run·step·phase·span | run manifest, CorrelationContext, EventRecorder의 trace_id·span_id·정확한 span timestamp ([manifest.py](../xlayer_telemetry/manifest.py), [events.py](../xlayer_telemetry/events.py)) | 외부 PID와 context를 연결하는 registry는 없음 |
| GPU·host | nvidia-smi sampler, Node Exporter의 CPU·memory·NIC·disk·filesystem ([run_telemetry.sh](../scripts/run_telemetry.sh), [gpu_sampler.py](../xlayer_telemetry/gpu_sampler.py)) | host NIC·disk total은 process나 run별 값이 아님 |
| vLLM·Ray | native /metrics target 등록과 진단 query ([source_discovery.py](../xlayer_telemetry/source_discovery.py), [diagnostics.py](../xlayer_telemetry/diagnostics.py)) | endpoint와 metric 활성화가 필요하고 shared engine을 run별로 자동 분리하지 않음 |
| RDMA·network | Node Exporter의 interface/RDMA counter와 별도 설정 가능한 query ([diagnostics.py](../xlayer_telemetry/diagnostics.py), [diagnosis.md](diagnosis.md)) | 기본은 interface 범위이며 NCCL·3FS flow ownership이 없음 |
| Disk·filesystem·SMART | Node Exporter와 optional smartctl exporter ([run_telemetry.sh](../scripts/run_telemetry.sh), [monitoring.md](monitoring.md)) | SMART는 장치 건강이고 application write latency가 아님 |
| 3FS | ClickHouse distributions를 시간 창과 선택적 filter로 조회 ([diagnostics.py](../xlayer_telemetry/diagnostics.py)) | shared-service 통계이며 USRBIO 호출이나 run별 client bytes는 없음 |
| Topology | 사용자가 선언한 component·edge를 metric으로 노출 ([topology_textfile.py](../xlayer_telemetry/topology_textfile.py)) | edge별 traffic은 측정된 값만 붙일 수 있음 |
| Comparison·rules | 같은 run·worker·boundary의 prior step 선택, signal delta, scope가 있는 candidate·counter·missing evidence ([diagnosis_analysis.py](../xlayer_telemetry/diagnosis_analysis.py), [diagnosis.schema.json](../config/diagnosis.schema.json)) | 각 rule의 필수 source가 없으면 strong 판정 불가 |
| Investigation | Bottleneck Summary, Cross-Layer Timeline, Step Explorer·Detail, Loki log, show_run ([diagnosis.md](diagnosis.md), [dashboards.md](dashboards.md)) | VERL step band는 근사치이고 sampled metric은 exact span이 아님 |
| Deep dive | 선택 rank profiler 예제와 NCCL baseline ([run_profile.sh](../scripts/run_profile.sh), [run_nccl_baseline.sh](../scripts/run_nccl_baseline.sh)) | profile은 별도 artifact이며 자동으로 Grafana profile backend에 들어가지 않음 |

현재 조사 경로는 이미 “run > 완료 step/update > stage 또는 span > cross-layer metric > baseline > candidate > evidence/counter/missing > Grafana”로 이어집니다.
특히 async trainer의 완료 step 창은 trainer update의 근사 경계이며 동시에 실행된 rollout의 소유 구간이라고 해석하지 않습니다.
eBPF로 이 기능을 다시 만들 이유는 없습니다.

## 3. Visibility Gap Analysis

| 질문 | 현재 답할 수 있는 수준 | eBPF가 줄 수 있는 것 | 먼저 확인할 더 작은 선택 |
| --- | --- | --- | --- |
| 어느 process가 disk I/O를 했나? | node·device 총량 | PID별 syscall·VFS latency, 일부 path·size | OpenTelemetry Host Metrics process scraper의 CPU·memory·disk I/O 합계 |
| 어느 file이 느린가? | 3FS shared latency와 mount·disk 지표 | 관측 가능한 POSIX/FUSE syscall의 fd·inode·path와 지연 | application/3FS client 계측, targeted BCC fileslower |
| 어느 process가 TCP flow를 만들었나? | interface 총량 | socket·endpoint·PID 또는 cgroup 단서 | 이미 있는 service metric과 explicit worker endpoint |
| 왜 CPU가 쉬고 있나? | CPU 사용률과 step duration | run queue 지연, off-CPU stack, lock·I/O wait 단서 | 짧은 BCC runqlat/offcputime 또는 기존 profiler |
| 어느 RPC가 느린가? | tool event를 application에서 넣으면 span 가능 | HTTP/gRPC RED metric과 일부 trace | tool/reward service에 EventRecorder 또는 Beyla |
| NCCL·3FS USRBIO의 실제 byte owner는? | NIC/RDMA interface와 3FS service 합계 | 일반 socket/syscall eBPF만으로는 불충분 | NCCL·3FS client counter 또는 명시적 library instrumentation |

PID별 disk I/O 합계에 eBPF가 필수라는 가정은 틀립니다.
[OpenTelemetry Host Metrics Receiver](https://github.com/open-telemetry/opentelemetry-collector-contrib/blob/main/receiver/hostmetricsreceiver/README.md)는 per-process CPU·memory·disk I/O scraper를 제공하고, Linux의 [/proc/PID/io](https://man7.org/linux/man-pages/man5/proc_pid_io.5.html)도 관련 counter를 노출합니다.
이 합계는 file path, syscall latency, 3FS USRBIO request, 원격 SSD byte attribution을 뜻하지 않습니다.
Linux /proc/PID/io는 기다린 child의 통계를 포함할 수 있고 read_bytes의 정확성도 block-backed filesystem에 대해 설명하므로, FUSE·shared client의 per-run 사용량으로 곧장 해석하지 않습니다.
따라서 process-level 합계만 필요한 첫 문제에는 eBPF 배포 전에 이 경로를 검증하는 편이 낫습니다.

## 4. What eBPF Can and Cannot Observe

### POSIX, io_uring, 3FS, and GPU Direct

일반 read/write·pread/pwrite·open 경로는 syscall 또는 VFS probe로 PID, size, fd/inode, 일부 path, latency를 조사할 수 있습니다.
fd와 path는 rename·mount namespace·짧은 process 수명 때문에 안정적인 전역 식별자가 아니며, page cache와 실제 device I/O도 다릅니다.
[io_uring](https://man7.org/linux/man-pages/man7/io_uring.7.html)은 제출과 완료가 분리되므로 단순 read/write syscall probe로 request latency나 원인 process를 완전하게 설명하지 못합니다.
O_DIRECT라도 VFS/block 경로를 탔다면 일부 지점은 보이지만 page-cache counter로 판단할 수 없습니다.

3FS의 [설계 문서](https://github.com/deepseek-ai/3FS/blob/main/docs/design_notes.md)는 FUSE client와 native client, RDMA 기반 service path를 구분합니다.
POSIX/FUSE의 application syscall은 관측 가능하지만 3FS service latency와 동일한 구간이 아닙니다.
[USRBIO API](https://github.com/deepseek-ai/3FS/blob/main/src/lib/api/UsrbIo.md)는 application과 FUSE process 사이 shared-memory ring에 request를 넣으므로 일반 read/write syscall probe가 각 USRBIO request를 자동으로 보지 못합니다.
USRBIO의 size·latency·client ownership이 중요하다면 3FS API 또는 client 측 계측이 더 직접적입니다.
[NVIDIA GPUDirect Storage 문서](https://docs.nvidia.com/gpudirect-storage/design-guide/)의 GPU와 storage 사이 DMA 경로도 CPU syscall count만으로 실제 전송량을 설명할 수 없습니다.

### Network, RDMA, and NCCL

TCP/UDP socket과 packet 계측은 endpoint·protocol·process 단서를 제공할 수 있습니다.
그러나 [Linux userspace verbs 문서](https://docs.kernel.org/infiniband/user_verbs.html)에 따르면 RDMA fast path는 userspace에서 mmap된 hardware register에 직접 접근해 syscall 없이 수행될 수 있습니다.
RDMA CM·verbs의 setup/slow path를 보거나 NIC aggregate counter를 읽는 것과 payload byte를 정확히 PID·run에 귀속하는 것은 다릅니다.
RoCE packet이 NIC에서 보여도 QP와 NCCL collective, 3FS USRBIO request, VERL phase를 자동 복원할 수 없습니다.
커널 버전·driver·tool의 실제 probe 지원을 검증하지 않은 “RDMA per-process bytes”는 evidence로 승격하지 않습니다.

### Scheduler and service calls

[BCC 도구](https://github.com/iovisor/bcc)의 runqlat, offcputime, fileslower, biosnoop 등은 scheduler·file·block 지연을 짧은 구간에서 확인하는 데 적합합니다.
block layer PID는 제출 process와 다를 수 있으므로 device latency만으로 application 소유권을 주장하지 않습니다.
HTTP/gRPC 서비스는 Beyla나 DeepFlow가 새 RED·trace evidence를 줄 수 있지만, trainer의 CUDA kernel·NCCL·USRBIO 의미까지 제공하지 않습니다.

## 5. DeepFlow Evaluation

[DeepFlow의 architecture](https://github.com/deepflowio/deepflow)는 host agent와 server를 분리하며, agent가 legacy/bare-metal host에서도 실행될 수 있습니다.
[Legacy host 배포 가이드](https://get.deepflow.io/docs/ce-install/legacy-host/)는 server를 Kubernetes 위에 둔다고 설명하지만, [All-in-One 가이드](https://www.deepflow.io/docs/ce-install/all-in-one/)는 Docker Compose server 경로도 제공합니다.
따라서 “bare metal agent 가능”과 “관리 backend가 가볍다”는 별도 판단입니다.
XLayer의 현재 Prometheus·Grafana·Loki·3FS ClickHouse에 추가 server·storage를 운영할 근거가 있는지 따져야 합니다.

DeepFlow는 TCP/UDP flow, 일부 application protocol, service map, request trace, file I/O event 및 profile을 제공하고, [PromQL](https://get.deepflow.io/docs/integration/output/query/promql/)과 [SQL query API](https://get.deepflow.io/docs/integration/output/query/sql/)가 있습니다.
[Agent 설정](https://get.deepflow.io/docs/configuration/agent/)에서 file I/O event의 기본 collect mode는 request lifecycle이며 all-event 수집은 별도 선택입니다.
따라서 training의 순수 file I/O가 기본 설정에서 모두 포착된다고 가정하면 안 됩니다.
[제품 edition 표](https://get.deepflow.io/docs/about/editions/)에 기능별 차이가 있으므로 특히 RDMA/GPU profiling은 사용할 edition에서 확인해야 합니다.
DeepFlow의 AI service·GPU 지원 설명은 제품 기능의 범위를 보여 주지만, 이 저장소의 VERL·NCCL·3FS workload에서 attribution과 overhead가 검증됐다는 뜻은 아닙니다.

DeepFlow UI는 flow·service 조사에 그대로 사용합니다.
XLayer integration을 만든다면 query API로 node·process·flow·시간 창의 소수 evidence만 가져와 기존 candidate의 missing_evidence를 채우고 DeepFlow의 상세 화면으로 이동합니다.
DeepFlow의 자체 trace ID를 XLayer trace_id와 동일하다고 가정하지 않고, 실제 propagation 또는 검증된 endpoint·time join이 없으면 related span으로 표시하지 않습니다.
현재 XLayer에 DeepFlow를 설치하거나 그 backend를 복제할 이유는 확인되지 않았습니다.

## 6. Beyla Evaluation

[Beyla](https://grafana.com/docs/beyla/latest/)는 Linux에서 zero-code HTTP/gRPC 중심 RED metric·trace를 만들고 optional [network flow bytes](https://grafana.com/docs/beyla/latest/network/)를 제공합니다.
[독립 process 실행](https://grafana.com/docs/beyla/latest/setup/)이 가능해 Kubernetes가 필수는 아니고 [Prometheus·OTLP export](https://grafana.com/docs/beyla/latest/configure/export-data/)를 지원합니다.
선택한 service의 process CPU·memory·disk metric도 제공하지만, 이는 file path·syscall latency와는 다릅니다.
Network metric의 기본 attribute는 주로 Kubernetes owner 중심이며 bare-metal에서는 endpoint·process 연결의 품질과 cardinality를 별도 측정해야 합니다.

VERL core에서는 vLLM·Ray의 native metric과 application span이 이미 가장 직접적인 신호입니다.
Beyla는 향후 tool server, sandbox API, reward server, inference RPC가 늘고 그 서비스의 HTTP/gRPC 지연이 실제 missing evidence가 될 때 더 유용합니다.
Python async service의 trace 연결과 generic span 상세도는 [Beyla의 명시적 한계](https://grafana.com/docs/beyla/latest/)를 따릅니다.
NCCL·RDMA·3FS USRBIO 전송의 소유권을 위해 Beyla를 도입하지 않습니다.
PoC를 하더라도 기존 vLLM native metric과 중복되지 않는 RPC latency 사례를 먼저 정해야 합니다.

## 7. Lightweight Alternatives and Direct Implementation

| 선택 | 적합한 질문 | XLayer에서의 위치 |
| --- | --- | --- |
| OpenTelemetry Host Metrics process scraper | PID별 CPU·memory·disk bytes | eBPF 없는 우선 검증; XLayer process identity와 join |
| BCC/bpftrace 또는 libbpf tools | 한 느린 interval의 file latency, size, run queue, off-CPU | targeted artifact; 결과를 기존 span·diagnosis 링크에서 열기 |
| Grafana Alloy pyroscope.ebpf | 지속 CPU profile이 이미 필요한 환경 | Pyroscope가 profile 저장·조회, XLayer는 링크 |
| DeepFlow | 많은 host의 flow·service·file event가 반복적으로 필요 | optional provider, query 결과를 evidence로 수용 |
| Beyla | tool/reward/inference HTTP·gRPC 서비스의 RED·trace 공백 | optional service provider |
| XLayer 자체 eBPF collector | 기존 도구로 검증한 좁은 signal을 제공할 수 없을 때만 | 현재 보류 |

[Grafana Alloy의 eBPF profiler](https://grafana.com/docs/alloy/latest/reference/components/pyroscope/pyroscope.ebpf/)는 별도 Pyroscope backend와 권한을 요구합니다.
XLayer의 현재 selected-rank PyTorch Profiler·Nsight 예제를 대체할 기본 dependency로 두지 않습니다.
직접 collector는 좁은 probe·안정적인 schema·명확한 유지보수 이점이 증명된 후에만 검토합니다.
Distributed tracing, service discovery, network topology inference, full profiling backend는 XLayer에서 직접 구현하지 않습니다.

## 8. Architecture Options

| Option | 얻는 evidence | 비용·한계 | 현재 판단 |
| --- | --- | --- | --- |
| A. No eBPF | 기존 workload·native·host·3FS 신호 | process attribution·wait 원인이 남음 | 기본 경로 유지 |
| B. DeepFlow | 광범위 flow/service/file event와 query API | server·agent·storage·권한·edition 비용 | 반복되는 flow gap을 확인한 뒤 optional |
| C. Beyla | 서비스 RPC RED·trace, 일부 network/process metric | training kernel path와 무관; bare-metal metadata 검증 필요 | tool/RPC workload에서 optional |
| D. Custom eBPF | 좁은 signal을 직접 schema에 맞춤 | kernel·probe·보안·성능 유지보수 | 현재 근거 부족 |
| E. Hybrid | 기본 XLayer + process scraper + 필요 시 targeted eBPF/provider | 선택·운영 기준 필요 | **권장** |

권장 순서는 A를 운영 기본으로 유지하고, 부족한 질문이 반복될 때 E의 작은 단계를 하나씩 추가하는 방식입니다.
현재는 DeepFlow와 Beyla를 상시 배포하지 않습니다.
이 결정은 제품 기능이 없다는 뜻이 아니라, 지금 필요한 증거와 도구의 비용이 아직 맞지 않는다는 판단입니다.

## 9. Process to XLayer Context Mapping

현재 CorrelationContext에는 run_id·role·worker_id·node·rank·local_rank·gpu가 있지만 PID 등록 필드는 없습니다.
eBPF 또는 process scraper의 PID를 이 context와 연결하려면 별도 identity join이 필요합니다.
안전한 key는 단순 PID가 아니라 **node identity + boot ID + PID + process start time**입니다.
PID는 재사용되며 node 이름만으로도 host 재시작을 구분할 수 없기 때문입니다.

~~~text
Application / launcher registration
  (node, boot_id, pid, start_time) > run, role, worker, rank, GPU
                                      |
Process scraper / eBPF source         |
  (node, boot_id, pid, start_time) ----+> verified process evidence

No exact registration
  > cgroup/container or parent tree hint
  > node/process scope with unresolved run ownership
~~~

우선순위는 explicit registration 또는 launcher manifest, 검증된 cgroup/container ID, Ray worker metadata, parent/child tree 순서입니다.
환경 변수 전체를 읽어 run을 추측하면 민감 정보와 권한 문제가 생기므로 사용하지 않습니다.
Ray가 process를 재시작하거나 worker를 재사용할 때는 매 시작·종료를 갱신하고, join 실패·PID 재사용·clock mismatch는 missing evidence로 남깁니다.
GPU 연결 역시 CUDA_VISIBLE_DEVICES만으로 물리 GPU 소유를 보장하지 않으므로 명시적 worker mapping을 우선합니다.
이 registry가 실제로 필요한지는 process scraper의 결과가 진단에 유의미한 사례를 확인한 뒤 결정합니다.

## 10. Diagnosis Integration and Observation Scope

eBPF 결과를 별도 dashboard에만 두지 않고 현재 DiagnosticEngine의 signal 입력과 candidate evidence에 붙입니다.
원본 source, 시각, measurement window, process/flow/device scope, sampling·drop 여부를 보존합니다.
현재 schema의 evidence·counter_evidence·missing_evidence를 그대로 사용하고 unsupported signal을 0으로 채우지 않습니다.

~~~text
Slow trainer update
  > storage_queue_saturation: supporting_signal
  > evidence: 3FS shared latency + storage SSD busy
  > missing: per-run client bytes, process/file path
       |
       +> verified vLLM PID + POSIX file writes during window
       |     scope=process/file; source=BCC or DeepFlow
       |     related span only if IDs or exact mapping agree
       |
       +> USRBIO path unobserved
             keep missing evidence; do not upgrade attribution
~~~

PID별 write bytes가 관측되어도 그 write가 KV offload인지 checkpoint인지는 file path 또는 application event가 있어야 구분됩니다.
Node/interface RDMA bytes는 eBPF 도입 후에도 flow ownership이 증명되지 않으면 network-interface scope로 유지합니다.
Slow step의 approximate boundary와 sampled metric을 exact span처럼 표현하지 않습니다.
Async mode에서는 trainer update와 동시 rollout의 소유권을 특히 구분합니다.

## 11. Overhead and Deployment Requirements

| 항목 | 검토할 내용 |
| --- | --- |
| Kernel | BTF, probe type, tracepoint/kprobe/uprobe 사용 가능 여부, driver·kernel upgrade 호환성 |
| 권한 | CAP_BPF·CAP_PERFMON, 필요한 경우 CAP_NET_ADMIN·CAP_NET_RAW·CAP_SYS_PTRACE·CAP_SYS_RESOURCE·CAP_SYS_ADMIN; /proc·tracefs 접근 |
| 보안 | container host PID/network namespace, AppArmor·SELinux·lockdown 정책, raw path·endpoint의 노출 범위 |
| 부하 | CPU 사용률, RSS·BPF map, event rate·lost event, export bytes, Prometheus cardinality, backend storage |
| 학습 영향 | token/s 또는 step/s, step p50·p95, rollout latency, GPU idle time, 3FS latency |

[Beyla 요구사항](https://grafana.com/docs/beyla/latest/)은 일반적으로 Linux 5.8+와 BTF이며 기능별 capability가 다릅니다.
[Beyla security 문서](https://grafana.com/docs/beyla/latest/security/)는 network socket filter, TC, application tracing의 권한 조합을 구분합니다.
[DeepFlow 배포 요구사항](https://get.deepflow.io/docs/ce-install/overview/)은 eBPF와 packet capture에 필요한 권한·SELinux 조건을 별도로 명시합니다.
Alloy pyroscope.ebpf는 [host PID namespace와 root 또는 해당 capability](https://grafana.com/docs/alloy/latest/reference/components/pyroscope/pyroscope.ebpf/)를 요구합니다.
Ubuntu 24.04라는 이름만으로 probe 성공이나 운영 허용 여부를 보장하지 않습니다.
AppArmor·SELinux·kernel lockdown과 container policy가 /proc·tracefs·BPF load를 제한하는지 실제 배포 방식에서 각각 확인해야 합니다.

이번 로컬 호스트의 read-only 사전 점검에서는 Ubuntu 24.04.4, kernel 6.17.0-35-generic, BTF 파일, bpftrace와 bpftool이 확인됐습니다.
이 점검은 BPF program load 권한, AppArmor 정책, DeepFlow/Beyla 호환성, production GPU node 성능을 검증한 것이 아닙니다.
이번 조사에서는 root probe를 실행하지 않았습니다.

실제 PoC를 시작한다면 먼저 eBPF 없는 baseline을 같은 workload·model·3FS path에서 3회 이상 기록합니다.
그다음 하나의 source만 켜고 순서를 교차한 A/B/A run에서 throughput, step p50·p95, host CPU·RSS, lost event, export volume, cardinality를 함께 측정합니다.
Training throughput 저하가 반복 측정 오차를 넘거나 event loss가 있으면 범위를 줄이거나 중단합니다.
항상 켤 signal은 낮은 event rate의 집계만 후보로 두고, path·stack·syscall event는 시간·PID·샘플 비율을 제한한 targeted 모드로 둡니다.
B300 등 큰 cluster에는 노드당 agent 부하와 중앙 저장·network 총량을 곱해 평가해야 합니다.

## 12. Recommendation and Decision Gates

**현재 선택은 E: staged hybrid이며 기본 배포는 A: no eBPF입니다.**
현 상태의 XLayer는 eBPF 없이도 핵심 진단 경로가 동작합니다.
가장 먼저 필요한 process disk bytes는 기존 OpenTelemetry process scraper로 검증할 수 있고, VERL·vLLM·3FS의 semantic gap은 eBPF만으로 해결되지 않습니다.
따라서 이번 조사에서는 production code나 root eBPF PoC를 추가하지 않습니다.
구체적인 원인 규명이 막힌 slow run이 아직 제시되지 않아 probe 하나를 선택해도 성공 기준을 정할 수 없기 때문입니다.

다음 순서로 작은 결정을 내립니다.

1. 반복되는 slow step에서 현재 candidate의 missing_evidence를 기록하고, process disk bytes가 실제로 필요한지 확인합니다.
2. 필요하면 기존 process scraper와 explicit PID/context mapping을 한 node에서 검증합니다.
3. 그 후에도 file I/O latency·size 또는 scheduler wait가 필요한 **한 사례**에만 BCC/bpftrace targeted PoC를 실행합니다.
4. 많은 node에서 TCP/service flow attribution이 지속적으로 필요하면 DeepFlow의 agent·server 비용과 query join 품질을 시험합니다.
5. tool/reward/inference RPC 지연이 문제라면 Beyla를 해당 서비스에만 시험합니다.
6. 기존 도구로 좁은 필수 signal을 안정적으로 얻지 못할 때만 custom collector를 다시 평가합니다.

PoC의 통과 조건은 “수치가 보인다”가 아닙니다.
기존 missing_evidence 하나가 실제 process 또는 flow scope의 검증 가능한 evidence로 바뀌고, run mapping·measurement loss·overhead를 설명할 수 있어야 합니다.
이 조건이 없으면 새 exporter나 dashboard를 추가하지 않습니다.

## 13. Answers and Future Work

| 질문 | 답 |
| --- | --- |
| Q1. 지금 eBPF가 꼭 필요한가? | 아니요. 현재 XLayer diagnosis는 eBPF 없이 작동하고, 실제 미해결 사례를 먼저 선정해야 합니다. |
| Q2. 무엇을 채울 수 있나? | 관측 가능한 POSIX file I/O의 PID·path·size·latency, TCP flow 단서, scheduler/off-CPU wait가 주요 후보입니다. |
| Q3. DeepFlow UI와 XLayer integration의 차이는? | DeepFlow UI는 service/flow 자체를 조사하고, XLayer는 그 중 확인된 결과를 run·step·phase candidate의 evidence로 연결합니다. |
| Q4. Beyla는 어디에 적합한가? | VERL core의 NCCL·3FS보다 HTTP/gRPC tool·reward·inference service에 적합합니다. |
| Q5. RDMA·NCCL·USRBIO의 한계는? | 일반 syscall/socket probe가 userspace fast path와 shared-memory request, DMA payload를 자동으로 보지 못합니다. |
| Q6. 자체 collector보다 기존 framework가 나은가? | 현재는 그렇습니다. process scraper와 targeted BCC/DeepFlow/Beyla가 우선입니다. |
| Q7. eBPF 없이 핵심 기능이 정상인가? | 예. VERL bridge·resource·3FS·baseline·rule·Grafana 경로는 optional source 없이도 기존 범위에서 동작합니다. |

향후 가장 가치 있는 구현 후보는 eBPF code가 아니라, 외부 process evidence를 XLayer context에 안전하게 연결하는 identity 계약입니다.
이 계약이 검증되어야 DeepFlow·Beyla·BCC 중 어떤 provider를 쓰더라도 observation scope를 올바르게 표시할 수 있습니다.
