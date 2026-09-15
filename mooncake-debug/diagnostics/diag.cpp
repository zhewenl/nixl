#include "diag.hpp"
namespace mcdiag {
thread_local Context* current=nullptr;
bool enabled() {static bool on=std::getenv("NIXL_MC_DIAG_FILE")!=nullptr;return on;}
uint64_t next_id(){static std::atomic<uint64_t> id{0};return ++id;}
void write(const Context& c) {
    static std::mutex lock;
    std::lock_guard<std::mutex> guard(lock);
    static FILE* file=std::fopen(std::getenv("NIXL_MC_DIAG_FILE"),"a");
    if(!file)std::abort();
    std::fprintf(file,"{\"pid\":%d,\"tid\":%ld,\"parent\":%lu,\"child\":%d,\"kind\":\"%s\",\"descriptors\":%lu,\"start_ns\":%lu,\"end_ns\":%lu,\"stages_ms\":{",
        getpid(),syscall(SYS_gettid),c.parent,c.child,c.kind,c.count,c.start,c.end);
    bool first=true;
    for(int i=0;i<StageCount;i++)if(c.calls[i]){
        std::fprintf(file,"%s\"%s\":%.9f",first?"":",",names[i],c.ns[i]/1e6);first=false;
    }
    std::fprintf(file,"},\"stage_calls\":{");first=true;
    for(int i=0;i<StageCount;i++)if(c.calls[i]){
        std::fprintf(file,"%s\"%s\":%lu",first?"":",",names[i],c.calls[i]);first=false;
    }
    std::fprintf(file,"},\"local_buffers\":%lu,\"target_buffers\":%lu,\"fine_stride\":%u,\"fine_attempts\":{",c.local_buffers,c.target_buffers,fine_stride());first=true;
    for(int i=0;i<StageCount;i++)if(c.fine_attempts[i]){
        std::fprintf(file,"%s\"%s\":%lu",first?"":",",names[i],c.fine_attempts[i]);first=false;
    }
    std::fprintf(file,"}}\n");std::fflush(file);
}
}
