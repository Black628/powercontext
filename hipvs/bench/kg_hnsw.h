// KG 图 → hnswlib HNSW 邻接表装配器。
//
// 为什么不改 hnswlib：HierarchicalNSW 的成员全是 public（hnswlib 0.8.0 hnswalg.h 只有一个
// `public:`），所以子类可以直接按 addPoint 的内存布局写入向量、层级与邻接表，
// 完全跳过 addPoint 内部的 searchBaseLayer 逐点贪心搜索。
//
// 与 seekdb 的关系：seekdb 的向量索引走 VSAG，而 VSAG 的 src/algorithm/hnswlib/hnswalg.h
// 就是 hnswlib 的 fork（同名函数、同样的 linklist 布局：count 在首 4 字节，随后是 tableint 数组）。
// 因此这里验证的装配逻辑可以平移到 VSAG（见 vsag_patch/README.md），
// 装配端只依赖「点已经存在」这一前提，不依赖搜索路径。
#pragma once

#include <atomic>
#include <cstdint>
#include <cstring>
#include <fstream>
#include <queue>
#include <stdexcept>
#include <string>
#include <thread>
#include <vector>

#include "hnswlib/hnswlib.h"

namespace kg {

struct KgFile {
    int32_t n{0};
    int32_t dim{0};
    int32_t k{0};
    int32_t M{0};
    int32_t ef_build{0};
    int32_t entry_id{0};
    std::vector<int32_t> levels;
    std::vector<int64_t> neighbors;   // n*k
    std::vector<float> distances;     // n*k
};

inline KgFile load_kg(const std::string& path) {
    std::ifstream in(path, std::ios::binary);
    if (!in) throw std::runtime_error("打不开 KG 文件: " + path);
    char magic[8];
    int32_t version = 0, reserved = 0;
    KgFile g;
    in.read(magic, 8);
    if (std::memcmp(magic, "KGVS1\0\0\0", 8) != 0) throw std::runtime_error("KG 魔数不匹配");
    in.read(reinterpret_cast<char*>(&version), 4);
    if (version != 1) throw std::runtime_error("KG 版本不支持");
    in.read(reinterpret_cast<char*>(&g.n), 4);
    in.read(reinterpret_cast<char*>(&g.dim), 4);
    in.read(reinterpret_cast<char*>(&g.k), 4);
    in.read(reinterpret_cast<char*>(&g.M), 4);
    in.read(reinterpret_cast<char*>(&g.ef_build), 4);
    in.read(reinterpret_cast<char*>(&g.entry_id), 4);
    in.read(reinterpret_cast<char*>(&reserved), 4);
    g.levels.resize(g.n);
    g.neighbors.resize(static_cast<size_t>(g.n) * g.k);
    g.distances.resize(static_cast<size_t>(g.n) * g.k);
    in.read(reinterpret_cast<char*>(g.levels.data()), 4LL * g.n);
    in.read(reinterpret_cast<char*>(g.neighbors.data()), 8LL * g.n * g.k);
    in.read(reinterpret_cast<char*>(g.distances.data()), 4LL * g.n * g.k);
    if (!in) throw std::runtime_error("KG 文件被截断: " + path);
    return g;
}

// 读取 Python 侧导出的裸 float32 向量（n*dim），避免在 C++ 里解析 .npy
inline std::vector<float> load_vectors(const std::string& path, int32_t expect_n, int32_t expect_dim) {
    std::ifstream in(path, std::ios::binary | std::ios::ate);
    if (!in) throw std::runtime_error("打不开向量文件: " + path);
    const auto bytes = static_cast<size_t>(in.tellg());
    in.seekg(0);
    const size_t expect = static_cast<size_t>(expect_n) * expect_dim * sizeof(float);
    if (bytes != expect) {
        throw std::runtime_error("向量文件大小不对: 期望 " + std::to_string(expect) + " 实际 " + std::to_string(bytes));
    }
    std::vector<float> v(static_cast<size_t>(expect_n) * expect_dim);
    in.read(reinterpret_cast<char*>(v.data()), static_cast<std::streamsize>(bytes));
    return v;
}

template <typename dist_t>
class KgIndex : public hnswlib::HierarchicalNSW<dist_t> {
public:
    using Base = hnswlib::HierarchicalNSW<dist_t>;
    using tableint = hnswlib::tableint;
    using linklistsizeint = hnswlib::linklistsizeint;
    using Cand = std::pair<dist_t, tableint>;
    using CandQueue = std::priority_queue<Cand, std::vector<Cand>, typename Base::CompareByFirst>;

    KgIndex(hnswlib::SpaceInterface<dist_t>* space, size_t max_elements, size_t M, size_t ef_construction)
        : Base(space, max_elements, M, ef_construction) {}

    // 按 KG 图直接装配：写向量、层级、各层邻接表，再补反向边。
    // 复杂度 O(n·k·dim)（只在需要距离时算），没有逐点 ef 次搜索。
    void assemble(const KgFile& g, const float* vectors, size_t M, size_t M0, int threads = 1) {
        const int n = g.n;
        const size_t dim = static_cast<size_t>(g.dim);
        if (static_cast<size_t>(g.dim) * sizeof(float) != this->data_size_) {
            throw std::runtime_error("向量维度与索引 data_size_ 不一致");
        }
        this->resizeIndex(static_cast<size_t>(n));

        int max_level = 0;
        for (int i = 0; i < n; i++) max_level = std::max<int>(max_level, g.levels[i]);
        this->maxlevel_ = max_level;

        // 1) 写入向量、标签、层级与上层邻接表的内存
        for (int i = 0; i < n; i++) {
            this->element_levels_[i] = g.levels[i];
            char* base = this->data_level0_memory_ + static_cast<size_t>(i) * this->size_data_per_element_;
            std::memset(base, 0, this->size_data_per_element_);
            std::memcpy(this->getDataByInternalId(i), vectors + static_cast<size_t>(i) * dim, this->data_size_);
            auto* label_ptr = reinterpret_cast<hnswlib::labeltype*>(this->getDataByInternalId(i) + this->data_size_);
            *label_ptr = static_cast<hnswlib::labeltype>(i);
            if (g.levels[i] > 0) {
                const size_t bytes = this->size_links_per_element_ * static_cast<size_t>(g.levels[i]) + 1;
                this->linkLists_[i] = static_cast<char*>(std::calloc(bytes, 1));
            } else {
                this->linkLists_[i] = nullptr;
            }
            this->label_lookup_[static_cast<hnswlib::labeltype>(i)] = static_cast<tableint>(i);
        }
        this->cur_element_count = static_cast<size_t>(n);
        this->enterpoint_node_ = static_cast<tableint>(g.entry_id);

        // 2) 逐层装配：候选 = 该点 kNN 列表里层级够高的邻居，用 hnswlib 自带的启发式裁剪到 M
        using Cand = std::pair<dist_t, tableint>;
        // CompareByFirst 是 HierarchicalNSW 的嵌套类型，必须用 Base:: 限定，
        // 否则 priority_queue 的类型与 getNeighborsByHeuristic2 的形参不匹配
        using CandQueue = std::priority_queue<Cand, std::vector<Cand>, typename Base::CompareByFirst>;
        for (int level = 0; level <= max_level; level++) {
            const size_t cap = (level == 0) ? M0 : M;
            auto do_range = [&](int begin, int end) {
                for (int i = begin; i < end; i++) {
                    if (g.levels[i] < level) continue;
                    CandQueue top_candidates;
                    const int64_t* row = &g.neighbors[static_cast<size_t>(i) * g.k];
                    for (int j = 0; j < g.k; j++) {
                        const int64_t nb = row[j];
                        if (nb < 0 || nb == i) continue;
                        if (g.levels[nb] < level) continue;
                        const dist_t d = this->fstdistfunc_(this->getDataByInternalId(i),
                                                           this->getDataByInternalId(static_cast<tableint>(nb)),
                                                           this->dist_func_param_);
                        top_candidates.emplace(d, static_cast<tableint>(nb));
                    }
                    if (top_candidates.empty()) continue;
                    this->getNeighborsByHeuristic2(top_candidates, cap);
                    write_links(i, level, top_candidates);
                }
            };
            if (threads <= 1) {
                do_range(0, n);
            } else {
                std::vector<std::thread> pool;
                const int chunk = (n + threads - 1) / threads;
                for (int t = 0; t < threads; t++) {
                    const int b = t * chunk, e = std::min(n, b + chunk);
                    if (b >= e) break;
                    pool.emplace_back(do_range, b, e);
                }
                for (auto& th : pool) th.join();
            }
        }

        // 3) 补反向边：kNN 图是单向的，而 HNSW 搜索依赖一定程度的双向连通
        add_reverse_edges(g, M, M0);
    }

    // 供 bench 观察：装配后各层平均出度，验证"确实写进去了"
    std::vector<double> avg_degree_per_level() const {
        std::vector<double> out;
        for (int level = 0; level <= this->maxlevel_; level++) {
            double sum = 0;
            size_t cnt = 0;
            for (size_t i = 0; i < this->cur_element_count; i++) {
                if (this->element_levels_[i] < level) continue;
                // getListCount 收的是非 const 指针（它只读，但不带 const 修饰）
                auto* ll = reinterpret_cast<linklistsizeint*>(this->get_linklist_at_level(static_cast<int>(i), level));
                sum += this->getListCount(ll);
                cnt++;
            }
            out.push_back(cnt ? sum / cnt : 0.0);
        }
        return out;
    }

private:
    void write_links(int internal_id, int level, std::vector<std::pair<dist_t, hnswlib::tableint>> picked) {
        char* data = reinterpret_cast<char*>(this->get_linklist_at_level(internal_id, level));
        auto* ll = reinterpret_cast<linklistsizeint*>(data);
        this->setListCount(ll, static_cast<linklistsizeint>(picked.size()));
        auto* dst = reinterpret_cast<tableint*>(ll + 1);
        for (size_t i = 0; i < picked.size(); i++) dst[i] = picked[i].second;
    }

    // 从 priority_queue 里取出启发式结果（getNeighborsByHeuristic2 会把结果留在这个 max-heap 里，
    // 但顺序是从远到近，写入时顺序无关紧要）
    void write_links(int internal_id, int level, CandQueue& top_candidates) {
        std::vector<std::pair<dist_t, hnswlib::tableint>> picked;
        picked.reserve(top_candidates.size());
        while (!top_candidates.empty()) {
            picked.push_back(top_candidates.top());
            top_candidates.pop();
        }
        write_links(internal_id, level, std::move(picked));
    }

    void add_reverse_edges(const KgFile& g, size_t M, size_t M0) {
        const int n = g.n;
        for (int level = 0; level <= this->maxlevel_; level++) {
            const size_t cap = (level == 0) ? M0 : M;
            std::vector<std::vector<tableint>> links(static_cast<size_t>(n));
            for (int i = 0; i < n; i++) {
                if (g.levels[i] < level) continue;
                const auto* ll = reinterpret_cast<const linklistsizeint*>(this->get_linklist_at_level(i, level));
                const size_t cnt = this->getListCount(const_cast<linklistsizeint*>(ll));
                const tableint* d = reinterpret_cast<const tableint*>(ll + 1);
                links[i].assign(d, d + cnt);
            }
            for (int i = 0; i < n; i++) {
                if (g.levels[i] < level) continue;
                for (const auto nb : links[i]) {
                    if (static_cast<int>(nb) == i) continue;
                    auto& back = links[nb];
                    if (back.size() >= cap) continue;
                    if (std::find(back.begin(), back.end(), static_cast<tableint>(i)) != back.end()) continue;
                    back.push_back(static_cast<tableint>(i));
                }
            }
            for (int i = 0; i < n; i++) {
                if (g.levels[i] < level) continue;
                char* data = reinterpret_cast<char*>(this->get_linklist_at_level(i, level));
                auto* ll = reinterpret_cast<linklistsizeint*>(data);
                this->setListCount(ll, static_cast<linklistsizeint>(links[i].size()));
                auto* dst = reinterpret_cast<tableint*>(ll + 1);
                std::memcpy(dst, links[i].data(), links[i].size() * sizeof(tableint));
            }
        }
    }
};

}  // namespace kg
