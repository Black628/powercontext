// HNSW 构建耗时对比：
//   (A) 标准构建  —— addPoint 逐点：每点 ef_construction 次贪心搜索（seekdb/VSAG 的同源路径）
//   (B) 图装配构建 —— 用外部 kNN 图（hipVS 在 GPU 上算出来的那张）直接写邻接表
// 两者用同一份向量、同一组 HNSW 参数，输出构建耗时、层出度、recall@k。
//
// 用法：
//   kg_hnsw_bench --vectors out/100k.vec --kg out/100k.kg --queries 200 --k 10 --threads 8 --json out.json
#include <algorithm>
#include <atomic>
#include <chrono>
#include <cmath>
#include <cstdlib>
#include <cstring>
#include <fstream>
#include <iostream>
#include <numeric>
#include <random>
#include <sstream>
#include <string>
#include <thread>
#include <unordered_set>
#include <vector>

#include "hnswlib/hnswlib.h"
#include "kg_hnsw.h"

namespace {

struct Args {
    std::string vectors;
    std::string kg;
    std::string json;
    int queries = 200;
    int k = 10;
    int threads = 1;
    int ef_search = 64;
    int repeat = 1;
    bool exact_bruteforce = true;
};

Args parse(int argc, char** argv) {
    Args a;
    for (int i = 1; i < argc; i++) {
        std::string s = argv[i];
        auto next = [&]() -> std::string { return (i + 1 < argc) ? argv[++i] : std::string(); };
        if (s == "--vectors") a.vectors = next();
        else if (s == "--kg") a.kg = next();
        else if (s == "--json") a.json = next();
        else if (s == "--queries") a.queries = std::stoi(next());
        else if (s == "--k") a.k = std::stoi(next());
        else if (s == "--threads") a.threads = std::stoi(next());
        else if (s == "--ef") a.ef_search = std::stoi(next());
        else if (s == "--repeat") a.repeat = std::max(1, std::stoi(next()));
        else if (s == "--no-exact") a.exact_bruteforce = false;
        else { std::cerr << "未知参数: " << s << "\n"; std::exit(2); }
    }
    if (a.vectors.empty() || a.kg.empty()) {
        std::cerr << "必须给 --vectors 与 --kg\n";
        std::exit(2);
    }
    return a;
}

double now_ms() {
    using clock = std::chrono::steady_clock;
    return std::chrono::duration<double, std::milli>(clock::now().time_since_epoch()).count();
}

// 精确 KNN：仅用于算 recall，查询集很小
std::vector<std::vector<uint32_t>> exact_knn(const float* v, int n, int dim,
                                             const std::vector<uint32_t>& queries, int k) {
    std::vector<std::vector<uint32_t>> out(queries.size());
    std::vector<std::pair<float, uint32_t>> buf(static_cast<size_t>(n));
    for (size_t qi = 0; qi < queries.size(); qi++) {
        const float* q = v + static_cast<size_t>(queries[qi]) * dim;
        for (int j = 0; j < n; j++) {
            const float* b = v + static_cast<size_t>(j) * dim;
            float acc = 0;
            for (int d = 0; d < dim; d++) {
                const float diff = q[d] - b[d];
                acc += diff * diff;
            }
            buf[j] = {acc, static_cast<uint32_t>(j)};
        }
        std::partial_sort(buf.begin(), buf.begin() + k, buf.end());
        out[qi].resize(k);
        for (int j = 0; j < k; j++) out[qi][j] = buf[j].second;
    }
    return out;
}

}  // namespace

int main(int argc, char** argv) {
    const Args a = parse(argc, argv);
    const auto kg = kg::load_kg(a.kg);
    const auto vectors = kg::load_vectors(a.vectors, kg.n, kg.dim);
    std::cout << "图: n=" << kg.n << " dim=" << kg.dim << " k=" << kg.k << " M=" << kg.M
              << " ef_build=" << kg.ef_build << " 线程=" << a.threads << "\n";

    // 余弦等价于归一化后的 L2；图是用 cosine 建的，所以这里也归一化
    std::vector<float> norm = vectors;
    for (int i = 0; i < kg.n; i++) {
        float* p = norm.data() + static_cast<size_t>(i) * kg.dim;
        double s = 0;
        for (int d = 0; d < kg.dim; d++) s += static_cast<double>(p[d]) * p[d];
        s = std::sqrt(s);
        if (s > 0) for (int d = 0; d < kg.dim; d++) p[d] = static_cast<float>(p[d] / s);
    }

    const size_t M = static_cast<size_t>(kg.M);
    const size_t M0 = 2 * M;
    hnswlib::L2Space space(static_cast<size_t>(kg.dim));

    // ---------- (A)+(B) 重复测量，取中位数（机器负载会漂，单次样本不可靠）----------
    auto build_std = [&]() -> double {
        auto* idx = new hnswlib::HierarchicalNSW<float>(
            &space, static_cast<size_t>(kg.n), M, static_cast<size_t>(kg.ef_build));
        const double t0 = now_ms();
        if (a.threads <= 1) {
            for (int i = 0; i < kg.n; i++) {
                idx->addPoint(norm.data() + static_cast<size_t>(i) * kg.dim, static_cast<hnswlib::labeltype>(i));
            }
        } else {
            std::vector<std::thread> pool;
            std::atomic<int> cursor{0};
            for (int t = 0; t < a.threads; t++) {
                pool.emplace_back([&]() {
                    for (;;) {
                        const int i = cursor.fetch_add(1);
                        if (i >= kg.n) break;
                        idx->addPoint(norm.data() + static_cast<size_t>(i) * kg.dim,
                                      static_cast<hnswlib::labeltype>(i));
                    }
                });
            }
            for (auto& th : pool) th.join();
        }
        const double ms = now_ms() - t0;
        delete idx;
        return ms;
    };

    auto build_kg = [&]() -> double {
        auto* idx = new kg::KgIndex<float>(&space, static_cast<size_t>(kg.n), M, static_cast<size_t>(kg.ef_build));
        const double t0 = now_ms();
        idx->assemble(kg, norm.data(), M, M0, a.threads);
        const double ms = now_ms() - t0;
        delete idx;
        return ms;
    };

    std::vector<double> std_samples, kg_samples;
    for (int r = 0; r < a.repeat; r++) {
        std_samples.push_back(build_std());
        kg_samples.push_back(build_kg());
        std::cout << "  重复 " << (r + 1) << "/" << a.repeat << ": 标准 " << std_samples.back()
                  << " ms, 图装配 " << kg_samples.back() << " ms\n";
    }
    auto median = [](std::vector<double> v) {
        std::sort(v.begin(), v.end());
        const size_t m = v.size() / 2;
        return v.size() % 2 ? v[m] : 0.5 * (v[m - 1] + v[m]);
    };
    const double base_ms = median(std_samples);
    const double kg_ms = median(kg_samples);

    // ---------- (C) 质量与结构（各建一次，用于检索）----------
    auto* base = new hnswlib::HierarchicalNSW<float>(&space, static_cast<size_t>(kg.n), M, static_cast<size_t>(kg.ef_build));
    for (int i = 0; i < kg.n; i++) {
        base->addPoint(norm.data() + static_cast<size_t>(i) * kg.dim, static_cast<hnswlib::labeltype>(i));
    }
    auto* kgs = new kg::KgIndex<float>(&space, static_cast<size_t>(kg.n), M, static_cast<size_t>(kg.ef_build));
    kgs->assemble(kg, norm.data(), M, M0, a.threads);

    // ---------- 质量：recall@k ----------
    std::mt19937 rng(12345);
    std::vector<uint32_t> queries;
    const int nq = std::min<int>(a.queries, kg.n);
    queries.resize(nq);
    for (int i = 0; i < nq; i++) queries[i] = static_cast<uint32_t>(rng() % static_cast<unsigned>(kg.n));

    double base_recall = -1, kg_recall = -1, kg_recall_full = -1;
    if (a.exact_bruteforce) {
        const auto truth = exact_knn(norm.data(), kg.n, kg.dim, queries, a.k);
        auto measure = [&](hnswlib::HierarchicalNSW<float>* idx) {
            double hit = 0;
            for (size_t qi = 0; qi < queries.size(); qi++) {
                const float* q = norm.data() + static_cast<size_t>(queries[qi]) * kg.dim;
                auto res = idx->searchKnn(q, static_cast<size_t>(a.k));
                std::unordered_set<uint32_t> got;
                while (!res.empty()) {
                    got.insert(static_cast<uint32_t>(res.top().second));
                    res.pop();
                }
                for (auto id : truth[qi]) if (got.count(id)) hit += 1;
            }
            return hit / (static_cast<double>(queries.size()) * a.k);
        };
        base->setEf(static_cast<size_t>(a.ef_search));
        kgs->setEf(static_cast<size_t>(a.ef_search));
        base_recall = measure(base);
        kg_recall = measure(kgs);
        kgs->setEf(static_cast<size_t>(std::max(200, a.ef_search * 4)));
        kg_recall_full = measure(kgs);
    }

    const auto deg0 = kgs->avg_degree_per_level();

    std::ostringstream js;
    js << "{\n"
       << "  \"n\": " << kg.n << ",\n"
       << "  \"dim\": " << kg.dim << ",\n"
       << "  \"kg_k\": " << kg.k << ",\n"
       << "  \"M\": " << kg.M << ",\n"
       << "  \"ef_construction\": " << kg.ef_build << ",\n"
       << "  \"threads\": " << a.threads << ",\n"
       << "  \"repeat\": " << a.repeat << ",\n"
       << "  \"build_std_ms\": " << base_ms << ",\n"
       << "  \"build_kg_ms\": " << kg_ms << ",\n"
       << "  \"speedup\": " << (kg_ms > 0 ? base_ms / kg_ms : 0.0) << ",\n"
       << "  \"std_samples_ms\": [";
    for (size_t i = 0; i < std_samples.size(); i++) js << (i ? ", " : "") << std_samples[i];
    js << "],\n  \"kg_samples_ms\": [";
    for (size_t i = 0; i < kg_samples.size(); i++) js << (i ? ", " : "") << kg_samples[i];
    js << "],\n";
    double load1 = -1.0;
#if defined(__linux__)
    double loads[3] = {0, 0, 0};
    if (getloadavg(loads, 3) == 3) load1 = loads[0];
#endif
    js << "  \"loadavg1\": " << load1 << ",\n"
       << "  \"recall_std\": " << base_recall << ",\n"
       << "  \"recall_kg\": " << kg_recall << ",\n"
       << "  \"recall_kg_ef4x\": " << kg_recall_full << ",\n"
       << "  \"ef_search\": " << a.ef_search << ",\n"
       << "  \"levels\": " << (kg.levels.empty() ? 0 : *std::max_element(kg.levels.begin(), kg.levels.end())) << ",\n"
       << "  \"avg_degree_level0\": " << (deg0.empty() ? 0.0 : deg0[0]) << "\n"
       << "}\n";

    std::cout << "标准构建 : " << base_ms << " ms\n"
              << "图装配   : " << kg_ms << " ms  (" << (kg_ms > 0 ? base_ms / kg_ms : 0.0) << "x)\n"
              << "recall@k : 标准 " << base_recall << " / 图装配 " << kg_recall
              << " (ef*4: " << kg_recall_full << ")\n"
              << "第0层平均出度: " << (deg0.empty() ? 0.0 : deg0[0]) << "\n";
    if (!a.json.empty()) {
        std::ofstream out(a.json);
        out << js.str();
        std::cout << "JSON → " << a.json << "\n";
    }

    delete base;
    delete kgs;
    return 0;
}
