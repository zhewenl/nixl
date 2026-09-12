// Tests the actual runtime with deterministic fake TE calls, not GPU semantics.
#include "mooncake_async.h"
#include "mooncake_legacy_drain.h"
#include <cassert>
#include <functional>
#include <iostream>
#include <map>
#include <cstring>
using namespace std::chrono_literals;
struct FakeBatch {
    size_t capacity;
    std::vector<transfer_request_t> requests;
    std::vector<bool> done;
    bool notify = false, notified = false;
};
std::mutex fake_mutex;
std::map<batch_id_t,FakeBatch> batches;
std::atomic<bool> allow_submit{true}, allow_data{true}, allow_poll{true};
std::atomic<int> submits{0}, queries{0}, frees{0}, notifications{0};
std::atomic<int> failed_index{-1}, timeout_index{-1}, query_errors{0}, submit_error{0};
std::atomic<bool> predispatch_failure{false};
uint64_t next_batch=1;
void waitFor(const std::function<bool()> &predicate) {
    auto end=std::chrono::steady_clock::now()+3s;
    while(!predicate()) {
        if(std::chrono::steady_clock::now()>end) { std::cerr<<"test deadline\n";std::abort(); }
        std::this_thread::sleep_for(1ms);
    }
}
extern "C" batch_id_t allocateBatchID(transfer_engine_t,size_t n) {
    std::lock_guard<std::mutex> lock(fake_mutex);
    auto id=next_batch++;batches.emplace(id,FakeBatch{n,{},{}});return id;
}
extern "C" int submitTransfer(transfer_engine_t,batch_id_t id,transfer_request_t *r,size_t n) {
    ++submits;waitFor([]{return allow_submit.load();});
    std::lock_guard<std::mutex> lock(fake_mutex);
    auto &b=batches.at(id);assert(n<=b.capacity);
    if(!predispatch_failure) { b.requests.assign(r,r+n);b.done.resize(n,false); }
    return submit_error;
}
extern "C" int submitTransferWithNotify(transfer_engine_t e,batch_id_t id,transfer_request_t *r,size_t n,notify_msg_t) {
    auto rc=submitTransfer(e,id,r,n);
    std::lock_guard<std::mutex> lock(fake_mutex);batches.at(id).notify=true;return rc;
}
extern "C" int getTransferStatus(transfer_engine_t,batch_id_t id,size_t i,transfer_status_t *s) {
    ++queries;waitFor([]{return allow_poll.load();});
    std::lock_guard<std::mutex> lock(fake_mutex);
    auto &b=batches.at(id);assert(i<b.requests.size());
    if(query_errors.load()>0) {--query_errors;return 1;}
    if(int(i)==timeout_index) {s->status=STATUS_TIMEOUT;return 0;}
    if(int(i)==failed_index) {s->status=STATUS_FAILED;b.done[i]=true;return 0;}
    if(!allow_data) {s->status=STATUS_WAITING;return 0;}
    auto &r=b.requests[i];
    if(!b.done[i]) std::memcpy(r.source,reinterpret_cast<void *>(r.target_offset),r.length);
    b.done[i]=true;s->status=STATUS_COMPLETED;s->transferred_bytes=r.length;
    bool all=true;for(bool done:b.done)all &= done;
    if(all && b.notify && !b.notified && failed_index<0) {++notifications;b.notified=true;}
    return 0;
}
extern "C" int freeBatchID(transfer_engine_t,batch_id_t id) {
    std::lock_guard<std::mutex> lock(fake_mutex);
    for(bool done:batches.at(id).done) if(!done)return 1;
    batches.erase(id);++frees;return 0;
}
bool nixlMooncakeLegacyDrainCompatible(){return true;}
int nixlMooncakePrepareFailedSubmitDrain(batch_id_t){return predispatch_failure?1:0;}
auto job(int *dst,int *src,size_t n=1,bool notify=false) {
    auto j=std::make_unique<nixlMooncakeAsync::Job>();
    j->completion=std::make_shared<nixlMooncakeCompletion>();
    for(size_t i=0;i<n;++i)j->requests.push_back({OPCODE_READ,dst+i,1,uint64_t(src+i),sizeof(int)});
    j->bytes=n*sizeof(int);j->has_notification=notify;j->sender="test";j->notification="once";return j;
}
void terminal(const std::shared_ptr<nixlMooncakeCompletion> &c,nixl_status_t expected) {
    waitFor([&]{return c->status.load()!=NIXL_IN_PROG;});assert(c->status==expected);
}
int main() {
    setenv("NIXL_MOONCAKE_ASYNC_MAX_JOBS","2",1);
    setenv("NIXL_MOONCAKE_ASYNC_POLL_US","100",1);
    int src[3]={17,28,39},dst[3]={0};
    {
        nixlMooncakeAsync runtime(nullptr);
        allow_submit=false;
        auto j=job(dst,src,3,true);auto a=j->completion;
        auto start=std::chrono::steady_clock::now();
        assert(runtime.enqueue(j)==NIXL_IN_PROG && !j);
        assert(std::chrono::steady_clock::now()-start<50ms);
        waitFor([]{return submits==1;});
        assert(runtime.busy() && a->status==NIXL_IN_PROG && frees==0);
        auto j2=job(dst,src);auto b=j2->completion;
        assert(runtime.enqueue(j2)==NIXL_IN_PROG);
        auto rejected=job(dst,src);
        assert(runtime.enqueue(rejected)==NIXL_ERR_NOT_ALLOWED && rejected);
        allow_poll=false;allow_submit=true;
        waitFor([]{return submits==2 && queries>0;}); // progress cannot starve submit
        assert(a->status==NIXL_IN_PROG && b->status==NIXL_IN_PROG);
        allow_poll=true;terminal(a,NIXL_SUCCESS);terminal(b,NIXL_SUCCESS);
        assert(dst[0]==17 && dst[1]==28 && dst[2]==39 && notifications==1);
        // New completion per generation: old DONE cannot complete a queued repost.
        allow_data=false;auto next=job(dst,src);auto c=next->completion;
        assert(runtime.enqueue(next)==NIXL_IN_PROG);
        assert(a->status==NIXL_SUCCESS && c->status==NIXL_IN_PROG);
        allow_data=true;terminal(c,NIXL_SUCCESS);
        // FAILED first task, second still active: never expose terminal/free early.
        failed_index=0;allow_data=false;
        auto bad=job(dst,src,3,true);auto d=bad->completion;
        assert(runtime.enqueue(bad)==NIXL_IN_PROG);
        auto old_queries=queries.load();waitFor([&]{return queries>old_queries+2;});
        assert(d->status==NIXL_IN_PROG && runtime.busy());
        allow_data=true;terminal(d,NIXL_ERR_BACKEND);assert(notifications==1);failed_index=-1;
        // Transient query failure is sticky error but waits for safe data drain.
        query_errors=1;auto q=job(dst,src);auto e=q->completion;
        assert(runtime.enqueue(q)==NIXL_IN_PROG);terminal(e,NIXL_ERR_BACKEND);
        // A timeout is not DMA completion; retain registration/admission ownership.
        timeout_index=0;auto t=job(dst,src);auto f=t->completion;
        assert(runtime.enqueue(t)==NIXL_IN_PROG);
        old_queries=queries;waitFor([&]{return queries>old_queries+2;});
        assert(f->status==NIXL_IN_PROG && runtime.busy());
        timeout_index=-1;terminal(f,NIXL_ERR_BACKEND);
        // Partial submitted error drains failed+pending tasks before free.
        submit_error=9;failed_index=0;allow_data=false;
        auto partial=job(dst,src,3);auto g=partial->completion;
        assert(runtime.enqueue(partial)==NIXL_IN_PROG);
        old_queries=queries;waitFor([&]{return queries>old_queries+2;});
        assert(g->status==NIXL_IN_PROG);
        allow_data=true;terminal(g,NIXL_ERR_BACKEND);failed_index=-1;
        // Route failure before dispatch can be reclaimed without querying null tasks.
        predispatch_failure=true;auto empty=job(dst,src);auto h=empty->completion;
        assert(runtime.enqueue(empty)==NIXL_IN_PROG);terminal(h,NIXL_ERR_BACKEND);
        submit_error=0;predispatch_failure=false;
        auto zero=job(dst,src,0);auto z=zero->completion;
        assert(runtime.enqueue(zero)==NIXL_IN_PROG);terminal(z,NIXL_SUCCESS);
    }
    // Shutdown joins workers and drains accepted work before the engine dies.
    allow_submit=false;
    auto runtime=std::make_unique<nixlMooncakeAsync>(nullptr);
    auto closing=job(dst,src);auto c=closing->completion;
    assert(runtime->enqueue(closing)==NIXL_IN_PROG);
    std::thread unblock([]{std::this_thread::sleep_for(50ms);allow_submit=true;});
    runtime.reset();unblock.join();assert(c->status==NIXL_SUCCESS);
    assert(batches.empty());
    std::cout<<"PASS: queue/bounds, independent progress, generations, partial failure, query error, timeout drain, pre-dispatch error, zero, shutdown\n";
}
