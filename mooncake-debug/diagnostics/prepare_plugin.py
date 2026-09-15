#!/usr/bin/env python3
from pathlib import Path
root=Path(__file__).resolve().parents[2]
source=root/'src/plugins/mooncake'
out=root/'.local/diagnostic-plugin-src'
diag=root/'.local/mooncake-diagnostic/mooncake-transfer-engine/src/diag.hpp'
h=(source/'mooncake_backend.h').read_text().replace('#include <vector>',f'#include "{diag}"\n#include <memory>\n#include <vector>')
h=h.replace('    mutable std::mutex mutex_;','    std::unique_ptr<mcdiag::Pool> submit_pool_;\n    mutable std::mutex mutex_;')
(out/'mooncake_backend.h').write_text(h)
s=(source/'mooncake_backend.cpp').read_text()
s=s.replace('    const std::string segment_name = chooseIpAddress();', '''    const char* count = std::getenv("NIXL_MC_SUBMIT_THREADS");
    if(count && (std::atoi(count)==4 || std::atoi(count)==1)) submit_pool_=std::make_unique<mcdiag::Pool>(std::atoi(count));
    const std::string segment_name = chooseIpAddress();''')
s=s.replace('nixlMooncakeEngine::~nixlMooncakeEngine() {','nixlMooncakeEngine::~nixlMooncakeEngine() {\n    submit_pool_.reset();')
s=s.replace('    uint64_t batch_id = INVALID_BATCH;', '''    struct Child {uint64_t batch=INVALID_BATCH;size_t count=0;};
    std::vector<Child> children;
    uint64_t diag_parent=0;
    bool parent_notify=false;
    std::string peer, message;
    uint64_t batch_id = INVALID_BATCH;''',1)
start=s.index('nixlMooncakeEngine::postXfer(');end=s.index('\nnixl_status_t\nnixlMooncakeEngine::checkXfer',start)
post=s[start:end]
post=post.replace('    auto priv = (nixlMooncakeBackendReqH *)handle;', '''    auto priv = (nixlMooncakeBackendReqH *)handle;
    priv->diag_parent=mcdiag::next_id();
    mcdiag::Scope scope(priv->diag_parent,-1,"post",local.descCount());
    mcdiag::Timer total(mcdiag::ParentPost);''',1)
post=post.replace('    if (priv->batch_id == INVALID_BATCH) {','    if (!submit_pool_ && priv->batch_id == INVALID_BATCH) {\n        mcdiag::Timer alloc(mcdiag::Allocate);',1)
post=post.replace('    transfer_request_t *request =', '    mcdiag::Timer build(mcdiag::BuildC);\n    transfer_request_t *request =',1)
post=post.replace('    int rc = 0;', '''    build.stop();
    if(submit_pool_) {
        priv->children.clear();
        const size_t chunk=(request_count+3)/4;
        const size_t chunks=(request_count+chunk-1)/chunk;
        priv->children.resize(chunks);
        priv->parent_notify=opt_args && opt_args->hasNotif;
        priv->peer=remote_agent;
        priv->message=priv->parent_notify?opt_args->notifMsg:"";
        std::vector<std::future<int>> futures;
        mcdiag::Timer dispatch(mcdiag::Dispatch);
        for(size_t c=0;c<chunks;c++) {
            size_t begin=c*chunk;
            size_t count=std::min(chunk,request_count-begin);
            uint64_t queued=mcdiag::enabled()?mcdiag::now():0;
            futures.push_back(submit_pool_->submit([&,c,begin,count,queued] {
                mcdiag::Scope child_scope(priv->diag_parent,int(c),"child_submit",count);
                if(mcdiag::enabled())mcdiag::add(mcdiag::QueueWait,mcdiag::now()-queued);
                auto& child=priv->children[c];child.count=count;
                {mcdiag::Timer alloc(mcdiag::Allocate);child.batch=allocateBatchID(engine_,count);}
                if(child.batch==INVALID_BATCH)return -1;
                mcdiag::Timer te(mcdiag::PluginTE);
                return submitTransfer(engine_,child.batch,request+begin,count);
            }));
        }
        dispatch.stop();
        int result=0;
        {mcdiag::Timer wait(mcdiag::Barrier);
         for(auto& future:futures)if(future.get()!=0)result=-1;}
        delete[] request;
        return result?NIXL_ERR_BACKEND:NIXL_IN_PROG;
    }
    mcdiag::Timer te(mcdiag::PluginTE);
    int rc = 0;''',1)
post=post.replace('    delete[] request;\n    if (rc)', '    te.stop();\n    delete[] request;\n    if (rc)',1)
s=s[:start]+post+s[end:]
start=s.index('nixlMooncakeEngine::checkXfer(');end=s.index('\nnixl_status_t\nnixlMooncakeEngine::releaseReqH',start)
check=s[start:end]
check=check.replace('    auto priv = (nixlMooncakeBackendReqH *)handle;', '''    auto priv = (nixlMooncakeBackendReqH *)handle;
    if(!priv->children.empty()) {
        bool all_done=true;
        for(size_t c=0;c<priv->children.size();c++) {
            auto& child=priv->children[c];
            if(child.batch==INVALID_BATCH)continue;
            mcdiag::Scope scope(priv->diag_parent,int(c),"child_check",child.count);
            scope.emit=false;
            mcdiag::Timer scan(mcdiag::CheckScan);
            bool done=true;
            for(size_t i=0;i<child.count;i++) {
                transfer_status_t status;
                int rc=getTransferStatus(engine_,child.batch,i,&status);
                if(rc||status.status==STATUS_FAILED)return NIXL_ERR_BACKEND;
                if(status.status==STATUS_PENDING||status.status==STATUS_WAITING){done=false;break;}
            }
            scan.stop();
            if(!done){all_done=false;continue;}
            {mcdiag::Timer free_time(mcdiag::FreeBatch);
             if(freeBatchID(engine_,child.batch))return NIXL_ERR_BACKEND;}
            child.batch=INVALID_BATCH;
            scope.emit=true;
        }
        if(!all_done)return NIXL_IN_PROG;
        if(priv->parent_notify){
            auto status=genNotif(priv->peer,priv->message);
            if(status!=NIXL_SUCCESS)return status;
            priv->parent_notify=false;
        }
        return NIXL_SUCCESS;
    }
    mcdiag::Scope scope(priv->diag_parent,-1,"check",priv->request_count);
    scope.emit=false;
    mcdiag::Timer scan(mcdiag::CheckScan);''',1)
check=check.replace('    if (!has_failed) {','    scan.stop();\n    if (!has_failed) {\n        mcdiag::Timer free_time(mcdiag::FreeBatch);\n        scope.emit=true;',1)
s=s[:start]+check+s[end:]
start=s.index('nixlMooncakeEngine::releaseReqH(')
s=s[:start]+s[start:].replace('    if (priv->batch_id != INVALID_BATCH) {','    for(auto& child:priv->children) {\n        if(child.batch!=INVALID_BATCH && freeBatchID(engine_,child.batch))return NIXL_ERR_BACKEND;\n    }\n    if (priv->batch_id != INVALID_BATCH) {',1)
(out/'mooncake_backend.cpp').write_text(s)
(out/'mooncake_plugin.cpp').write_text((source/'mooncake_plugin.cpp').read_text())
