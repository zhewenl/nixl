// Empty Timer intervals on the same host; estimates fixed clock/Timer cost only.
#include "diag.hpp"
#include <algorithm>
#include <iostream>
namespace mcdiag { thread_local Context* current=nullptr; }
int main() {
    mcdiag::Context context{1,0,0,0,-1,"clock",{}, {}};
    mcdiag::current=&context;
    constexpr size_t groups=1000, per_group=1000;
    std::vector<double> means;
    for(size_t group=0;group<groups;group++) {
        uint64_t before=context.ns[mcdiag::LocalSelect];
        for(size_t i=0;i<per_group;i++) {
            mcdiag::Timer timer(mcdiag::LocalSelect);
            asm volatile("" ::: "memory");
            timer.stop();
        }
        means.push_back(double(context.ns[mcdiag::LocalSelect]-before)/per_group);
    }
    std::sort(means.begin(),means.end());
    std::cout<<"{\"intervals\":"<<groups*per_group
             <<",\"mean_empty_interval_ns\":"<<double(context.ns[mcdiag::LocalSelect])/(groups*per_group)
             <<",\"median_group_mean_ns\":"<<means[groups/2]
             <<",\"p90_group_mean_ns\":"<<means[groups*9/10]<<"}\n";
}
