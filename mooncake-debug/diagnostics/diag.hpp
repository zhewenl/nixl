#pragma once
#include <array>
#include <atomic>
#include <chrono>
#include <condition_variable>
#include <cstdio>
#include <cstdlib>
#include <deque>
#include <functional>
#include <future>
#include <mutex>
#include <thread>
#include <vector>
#include <unistd.h>
#include <sys/syscall.h>

namespace mcdiag {
enum Stage { Allocate, BuildC, Dispatch, QueueWait, Barrier, CConvert, TENative,
             TaskSelect, RdmaTotal, TargetMap, QueueLock, Enqueue, PluginTE,
             CheckScan, FreeBatch, DestroyTasks, ParentPost, LocalSelect, SliceCalculate, SourceBoundary, TargetBoundary, SliceAllocate, SliceInitialize, PeerSelect, SelectTopology, StageCount };
inline const char* names[] = {"allocate_batch", "build_c_requests", "dispatch", "queue_wait",
    "submit_barrier", "c_to_cpp", "te_native_submit", "task_init_transport_select", "rdma_submit",
    "target_mapping_grouping", "queue_lock_wait", "queue_enqueue", "plugin_te_wrapper",
    "check_scan", "free_batch", "destroy_tasks_slices", "parent_post", "local_select_device", "slice_calculate", "source_boundary", "target_boundary", "slice_allocate", "slice_initialize_group", "peer_select_device", "select_topology"};
inline uint64_t now() {
    return std::chrono::duration_cast<std::chrono::nanoseconds>(
        std::chrono::steady_clock::now().time_since_epoch()).count();
}
struct Context {
    uint64_t parent, start, end, count;
    int child;
    const char* kind;
    std::array<uint64_t, StageCount> ns{};
    std::array<uint64_t, StageCount> calls{};
    std::array<uint64_t, StageCount> fine_attempts{};
    uint64_t local_buffers=0, target_buffers=0;
};
extern thread_local Context* current;
bool enabled();
uint64_t next_id();
void write(const Context& c);
inline void add(Stage stage, uint64_t ns) {
    if(current) {current->ns[stage] += ns; current->calls[stage]++;}
}
struct Timer {
    Context* ctx;
    Stage stage;
    uint64_t start;
    Timer(Stage s, bool active=true): ctx(active ? current : nullptr), stage(s), start(ctx ? now() : 0) {}
    void stop() {if(ctx) {ctx->ns[stage]+=now()-start;ctx->calls[stage]++;ctx=nullptr;}}
    ~Timer() {stop();}
};
inline bool fine_enabled() {static bool on=[] {const char* p=std::getenv("NIXL_MC_FINE_TIMERS");return p && p[0]=='1';}();return on;}
inline unsigned fine_stride() {static unsigned stride=[] {const char* p=std::getenv("NIXL_MC_FINE_STRIDE");return p ? std::max(1, std::atoi(p)) : 1;}();return stride;}
inline bool sample_fine(Stage s) {
    if(!fine_enabled() || !current)return false;
    const uint64_t index=current->fine_attempts[s]++;
    return (index + current->parent*17 + static_cast<unsigned>(s)*7) % fine_stride()==0;
}
struct FineTimer: Timer {explicit FineTimer(Stage s): Timer(s,sample_fine(s)) {}};
struct Scope {
    Context data;
    Context* previous;
    bool emit=true;
    Scope(uint64_t parent, int child, const char* kind, uint64_t count):
        data{parent,now(),0,count,child,kind,{},{}}, previous(current) {
        current=enabled()?&data:nullptr;
    }
    ~Scope() {data.end=now();current=previous;if(enabled()&&emit)write(data);}
};
class Pool {
    std::mutex lock;
    std::condition_variable cv;
    std::deque<std::function<void()>> tasks;
    bool stop=false;
    std::vector<std::thread> threads;
public:
    explicit Pool(int n) {
        for(int i=0;i<n;i++) threads.emplace_back([this]{
            while(true){
                std::function<void()> fn;
                {std::unique_lock<std::mutex> guard(lock);
                 cv.wait(guard,[this]{return stop||!tasks.empty();});
                 if(stop&&tasks.empty())return;
                 fn=std::move(tasks.front());tasks.pop_front();}
                fn();
            }
        });
    }
    std::future<int> submit(std::function<int()> fn) {
        auto task=std::make_shared<std::packaged_task<int()>>(std::move(fn));
        auto future=task->get_future();
        {std::lock_guard<std::mutex> guard(lock);tasks.emplace_back([task]{(*task)();});}
        cv.notify_one();return future;
    }
    ~Pool() {
        {std::lock_guard<std::mutex> guard(lock);stop=true;}
        cv.notify_all();for(auto& t:threads)t.join();
    }
};
}
