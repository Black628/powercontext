// KG 图读取 + HNSW 邻接表装配（VSAG 侧）。
//
// 与 hipvs/bench/kg_hnsw.h 是同一套算法，适配 VSAG fork 的成员命名：
//   hnswlib: linkLists_  / data_level0_memory_(char*)  / element_levels_(vector<int>)
//   vsag   : link_lists_ / data_level0_memory_(BlockManager*) / element_levels_(int*)
//
// 用模板而不是直接吃 HierarchicalNSW&，避免本头与 hnswalg.h 循环包含。
// 图格式见 hipvs/graphbuild/kgformat.py。
#pragma once

#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <fstream>
#include <string>
#include <vector>

namespace hnswlib {

struct KgGraphFile {
    int64_t num_elements{0};
    int64_t dim{0};
    int64_t graph_k{0};
    int64_t M{0};
    int64_t M0{0};
    int64_t ef_construction{0};
    int32_t entry_id{0};
    std::vector<int32_t> levels;
    std::vector<int64_t> neighbors;
    std::vector<float> distances;

    static bool
    Load(const char* path, KgGraphFile& out) {
        std::ifstream in(path, std::ios::binary);
        if (!in) {
            return false;
        }
        char magic[8];
        int32_t version = 0;
        int32_t n32 = 0, dim32 = 0, k32 = 0, m32 = 0, ef32 = 0, entry = 0, reserved = 0;
        in.read(magic, 8);
        if (std::memcmp(magic, "KGVS1\0\0\0", 8) != 0) {
            return false;
        }
        in.read(reinterpret_cast<char*>(&version), 4);
        if (version != 1) {
            return false;
        }
        in.read(reinterpret_cast<char*>(&n32), 4);
        in.read(reinterpret_cast<char*>(&dim32), 4);
        in.read(reinterpret_cast<char*>(&k32), 4);
        in.read(reinterpret_cast<char*>(&m32), 4);
        in.read(reinterpret_cast<char*>(&ef32), 4);
        in.read(reinterpret_cast<char*>(&entry), 4);
        in.read(reinterpret_cast<char*>(&reserved), 4);

        out.num_elements = n32;
        out.dim = dim32;
        out.graph_k = k32;
        out.M = m32;
        out.M0 = 2 * static_cast<int64_t>(m32);
        out.ef_construction = ef32;
        out.entry_id = entry;
        const size_t n = static_cast<size_t>(out.num_elements);
        const size_t k = static_cast<size_t>(out.graph_k);
        out.levels.resize(n);
        out.neighbors.resize(n * k);
        out.distances.resize(n * k);
        in.read(reinterpret_cast<char*>(out.levels.data()), 4LL * out.num_elements);
        in.read(reinterpret_cast<char*>(out.neighbors.data()), 8LL * out.num_elements * out.graph_k);
        in.read(reinterpret_cast<char*>(out.distances.data()), 4LL * out.num_elements * out.graph_k);
        return static_cast<bool>(in);
    }
};

// Index 需具备（VSAG 的 HierarchicalNSW 都有）：
//   data_level0_memory_(BlockManager*)、link_lists_、element_levels_、label_lookup_、
//   setListCount/getListCount、getDataByInternalId、fstdistfunc_、dist_func_param_、
//   getNeighborsByHeuristic2、size_data_per_element_、size_links_per_element_、data_size_、
//   resizeIndex、cur_element_count、enterpoint_node_、maxlevel_
template <typename Index, typename TableInt, typename LabelType, typename Heap>
inline int64_t
assemble_from_kg_graph(Index& index,
                       const KgGraphFile& g,
                       const float* vectors) {
    const size_t n = static_cast<size_t>(g.num_elements);
    const size_t graph_k = static_cast<size_t>(g.graph_k);
    const size_t dim = index.data_size_ / sizeof(float);
    const int32_t* levels = g.levels.data();
    const int64_t* neighbors = g.neighbors.data();

    int32_t max_level = 0;
    for (size_t i = 0; i < n; i++) {
        if (levels[i] > max_level) {
            max_level = levels[i];
        }
    }
    index.maxlevel_ = max_level;

    // 1) 向量 / 标签 / 层级 / 上层链表内存
    for (size_t i = 0; i < n; i++) {
        index.element_levels_[i] = levels[i];
        std::memset(index.data_level0_memory_->GetElementPtr(i, 0), 0, index.size_data_per_element_);
        std::memcpy(index.getDataByInternalId(static_cast<TableInt>(i)), vectors + i * dim, index.data_size_);
        const auto label = static_cast<LabelType>(i);
        std::memcpy(index.data_level0_memory_->GetElementPtr(i, index.label_offset_),
                    &label,
                    sizeof(LabelType));
        if (levels[i] > 0) {
            const size_t bytes = index.size_links_per_element_ * static_cast<size_t>(levels[i]) + 1;
            index.link_lists_[i] = static_cast<char*>(std::calloc(bytes, 1));
        } else {
            index.link_lists_[i] = nullptr;
        }
        index.label_lookup_[label] = static_cast<TableInt>(i);
    }
    index.cur_element_count = n;
    index.enterpoint_node_ = static_cast<TableInt>(g.entry_id);

    // 2) 逐层装配：kNN 邻居里层级够高的，用 vsag 自带的启发式裁剪
    for (int32_t level = 0; level <= max_level; level++) {
        const size_t cap = (level == 0) ? static_cast<size_t>(g.M0) : static_cast<size_t>(g.M);
        for (size_t i = 0; i < n; i++) {
            if (levels[i] < level) {
                continue;
            }
            Heap top_candidates;
            const int64_t* row = neighbors + i * graph_k;
            for (size_t j = 0; j < graph_k; j++) {
                const int64_t nb = row[j];
                if (nb < 0 || nb == static_cast<int64_t>(i) || nb >= g.num_elements) {
                    continue;
                }
                if (levels[nb] < level) {
                    continue;
                }
                const float d = index.fstdistfunc_(index.getDataByInternalId(static_cast<TableInt>(i)),
                                                   index.getDataByInternalId(static_cast<TableInt>(nb)),
                                                   index.dist_func_param_);
                top_candidates.emplace(d, static_cast<TableInt>(nb));
            }
            if (top_candidates.empty()) {
                continue;
            }
            index.getNeighborsByHeuristic2(top_candidates, cap);
            auto* ll = index.getLinklistAtLevel(static_cast<TableInt>(i), static_cast<int>(level));
            index.setListCount(ll, static_cast<unsigned short>(top_candidates.size()));
            auto* dst = reinterpret_cast<TableInt*>(ll + 1);
            size_t w = 0;
            while (!top_candidates.empty()) {
                dst[w++] = top_candidates.top().second;
                top_candidates.pop();
            }
        }
    }

    // 3) 反向边：kNN 图是单向的，HNSW 搜索需要一定的双向连通
    for (int32_t level = 0; level <= max_level; level++) {
        const size_t cap = (level == 0) ? static_cast<size_t>(g.M0) : static_cast<size_t>(g.M);
        std::vector<std::vector<TableInt>> adj(n);
        for (size_t i = 0; i < n; i++) {
            if (levels[i] < level) {
                continue;
            }
            auto* ll = index.getLinklistAtLevel(static_cast<TableInt>(i), static_cast<int>(level));
            const size_t cnt = index.getListCount(ll);
            const auto* d = reinterpret_cast<const TableInt*>(ll + 1);
            adj[i].assign(d, d + cnt);
        }
        for (size_t i = 0; i < n; i++) {
            if (levels[i] < level) {
                continue;
            }
            for (const auto nb : adj[i]) {
                auto& back = adj[nb];
                if (back.size() >= cap) {
                    continue;
                }
                bool has = false;
                for (const auto v : back) {
                    if (v == static_cast<TableInt>(i)) {
                        has = true;
                        break;
                    }
                }
                if (!has) {
                    back.push_back(static_cast<TableInt>(i));
                }
            }
        }
        for (size_t i = 0; i < n; i++) {
            if (levels[i] < level) {
                continue;
            }
            auto* ll = index.getLinklistAtLevel(static_cast<TableInt>(i), static_cast<int>(level));
            index.setListCount(ll, static_cast<unsigned short>(adj[i].size()));
            if (!adj[i].empty()) {
                std::memcpy(reinterpret_cast<TableInt*>(ll + 1),
                            adj[i].data(),
                            adj[i].size() * sizeof(TableInt));
            }
        }
    }
    return static_cast<int64_t>(n);
}

}  // namespace hnswlib
