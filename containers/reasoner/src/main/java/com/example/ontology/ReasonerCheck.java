package com.example.ontology;

import java.io.File;
import java.io.InputStream;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Properties;
import java.util.Set;
import java.util.TreeSet;

import org.semanticweb.elk.owlapi.ElkReasoner;
import org.semanticweb.elk.owlapi.ElkReasonerFactory;
import org.semanticweb.elk.reasoner.completeness.IncompleteResult;
import org.semanticweb.elk.reasoner.completeness.Incompleteness;
import org.semanticweb.elk.reasoner.completeness.IncompletenessMonitor;
import org.semanticweb.owlapi.apibinding.OWLManager;
import org.semanticweb.owlapi.model.OWLClass;
import org.semanticweb.owlapi.model.OWLOntology;
import org.semanticweb.owlapi.model.OWLOntologyManager;
import org.semanticweb.owlapi.profiles.OWL2ELProfile;
import org.semanticweb.owlapi.profiles.OWLProfileReport;
import org.semanticweb.owlapi.profiles.OWLProfileViolation;
import org.semanticweb.owlapi.reasoner.Node;

/**
 * OWL 推論器 (ELK) で論理的整合性と充足不能クラスを検査する (P2B-01、ADR-0021)。
 *
 * <p><b>なぜ機械が判定するのか。</b> ADR-0009 決定1 は「形式的に決定可能なもの
 * (構文、プロファイル適合、論理的整合性)は機械が確定的に判定する」と決めた。
 * 「論理的矛盾を人が目で確認する設計にはしない。推論器の仕事である」。
 *
 * <p><b>この検査のいちばん重要な性質。</b> ELK は OWL 2 EL の推論器であり、
 * 扱えない公理は<b>無視する</b>。無視すると制約が減るので導出も減る。
 * つまり<b>誤検出はしないが、見逃しはする</b>(健全だが不完全)。
 * だからこの検査は「矛盾がない」とは言わない。<b>「矛盾は検出されなかった」</b>
 * と言い、同時に<b>その結論が完全だったかどうか</b>を必ず添える
 * (ADR-0021 決定1)。ELK 自身が {@link IncompletenessMonitor} で教えてくれる。
 *
 * <p><b>OWLAPI の {@code isConsistent()} を使わない理由。</b> あれは
 * {@code IncompleteResult} を剥がして {@code boolean} だけを返すので、
 * <b>「完全に検査できた false」と「見逃しがあるかもしれない false」が
 * 区別できなくなる</b>。ELK 拡張の {@code checkIsConsistent()} を使う。
 *
 * <p><b>プロファイル適合から不完全性を推測してはいけない。</b> 実測で両方向に
 * ずれた: データプロパティは OWL 2 EL に<b>含まれる</b>のに ELK は扱えず
 * (逸脱 0 件、しかし不完全)、subClassOf の左辺の {@code owl:unionOf} は
 * 逸脱として報告されるのに ELK は完全に扱えた(逸脱 1 件、しかし完全)。
 *
 * <p><b>出力は標準出力の JSON、ログは標準エラー。</b> 呼び出し側(シェル)が
 * 結果を機械的に読めるようにする。
 */
public final class ReasonerCheck {

    /** OWL 2 EL プロファイル逸脱のうち「宣言が無い」だけのものを表す型名。 */
    private static final String UNDECLARED_PREFIX = "UseOfUndeclared";

    /** 同上。OWLAPI が古い形式で報告するときの型名。 */
    private static final String UNDECLARED_LEGACY = "UndeclaredEntityViolation";

    /**
     * JSON に載せるエラーメッセージの上限。
     *
     * <p>OWLAPI の {@code UnparsableOntologyException} は<b>試した全パーサの
     * ログを連結した数万文字</b>を返す(実測)。JSON を読める大きさに保つため
     * ここで切るが、<b>全文は標準エラーに出す</b>ので診断情報は失われない。
     */
    private static final int MAX_ERROR_CHARS = 2000;

    private ReasonerCheck() {
    }

    /** JSON の文字列をエスケープする(依存を増やさないため手で書く)。 */
    private static String q(String value) {
        StringBuilder out = new StringBuilder("\"");
        for (char c : value.toCharArray()) {
            switch (c) {
                case '"' -> out.append("\\\"");
                case '\\' -> out.append("\\\\");
                case '\n' -> out.append("\\n");
                case '\r' -> out.append("\\r");
                case '\t' -> out.append("\\t");
                default -> {
                    if (c < 0x20) {
                        out.append(String.format("\\u%04x", (int) c));
                    } else {
                        out.append(c);
                    }
                }
            }
        }
        return out.append('"').toString();
    }

    /** 値か JSON の {@code null}。「値が無い」を空文字列で表さないため。 */
    private static String qOrNull(String value) {
        return value == null ? "null" : q(value);
    }

    private static String jsonList(List<String> values) {
        StringBuilder out = new StringBuilder("[");
        for (int i = 0; i < values.size(); i++) {
            if (i > 0) {
                out.append(", ");
            }
            out.append(q(values.get(i)));
        }
        return out.append(']').toString();
    }

    /**
     * 値か JSON の {@code null}。
     *
     * <p><b>「測ったら空だった」と「測っていない」を区別する</b>ため、後者を
     * {@code []} ではなく {@code null} で返す(ADR-0020 の健全性指標と同じ扱い)。
     */
    private static String jsonListOrNull(List<String> values) {
        return values == null ? "null" : jsonList(values);
    }

    /**
     * 実際に走った ELK のバージョン。
     *
     * <p><b>結論はどの推論器が出したかに依存する。</b> 不完全性の範囲も版で変わる
     * ので、出力に版を刻む(ADR-0021 決定1)。{@code getReasonerName()} は
     * ELK 0.6.0 では {@code null} を返す(実測)ため使わない。
     */
    private static String reasonerVersion() {
        String resource = "/META-INF/maven/io.github.liveontologies/elk-owlapi/pom.properties";
        try (InputStream in = ReasonerCheck.class.getResourceAsStream(resource)) {
            if (in == null) {
                return null;
            }
            Properties props = new Properties();
            props.load(in);
            String version = props.getProperty("version");
            return version == null ? null : "ELK " + version;
        } catch (Exception exc) {
            // 版が読めなかったこと自体は検査の結論に影響しない。null で見せる。
            return null;
        }
    }

    /** 1 ファイルの検査結果。 */
    private record Result(
            String file,
            boolean loaded,
            String error,
            int axiomCount,
            boolean inconsistencyFound,
            boolean consistencyConclusive,
            List<String> unsatisfiableClasses,
            boolean unsatisfiableClassesConclusive,
            List<String> incompletenessReasons,
            List<String> expressivityViolations,
            List<String> declarationViolations) {

        /**
         * ビルドを止めるべきか。
         *
         * <p><b>止めるのは「確実に悪い」ものだけである。</b> ELK は誤検出しない
         * ので、矛盾と充足不能クラスは検出されたら本物である(ADR-0021 決定1)。
         *
         * <p><b>結論が不完全だったことでは止めない</b>(ADR-0021 決定2)。
         * データプロパティを 1 つ書くだけで ELK は不完全になるため、止める設計に
         * すると<b>実用的なオントロジーがほぼ全部落ちる</b>(実測: 同梱サンプルは
         * データプロパティ 2 個で不完全になる)。EL の外を書くのは OWL 2 DL として
         * 正当な選択であり、CI が禁じることではない。<b>その代わり必ず報告する。</b>
         *
         * <p><b>プロファイル逸脱でも止めない</b>(ADR-0021 決定3)。逸脱は
         * 「矛盾している」ことを意味しない。
         */
        boolean blocking() {
            if (!loaded || inconsistencyFound) {
                return true;
            }
            return unsatisfiableClasses != null && !unsatisfiableClasses.isEmpty();
        }

        String toJson() {
            Map<String, String> fields = new LinkedHashMap<>();
            fields.put("file", q(file));
            fields.put("loaded", String.valueOf(loaded));
            fields.put("error", qOrNull(error));
            fields.put("axiom_count", String.valueOf(axiomCount));
            // **名前が「矛盾がない」ではなく「矛盾が見つかった」である**のは意図的。
            // 見逃しがありうる検査の結果を「整合している」と呼んではいけない。
            fields.put("inconsistency_found", String.valueOf(inconsistencyFound));
            fields.put("consistency_conclusive", String.valueOf(consistencyConclusive));
            fields.put("unsatisfiable_classes", jsonListOrNull(unsatisfiableClasses));
            fields.put(
                    "unsatisfiable_class_count",
                    unsatisfiableClasses == null
                            ? "null"
                            : String.valueOf(unsatisfiableClasses.size()));
            fields.put(
                    "unsatisfiable_classes_conclusive",
                    String.valueOf(unsatisfiableClassesConclusive));
            fields.put("incompleteness_reasons", jsonList(incompletenessReasons));
            fields.put("expressivity_violations", jsonList(expressivityViolations));
            fields.put(
                    "expressivity_violation_count", String.valueOf(expressivityViolations.size()));
            fields.put("declaration_violations", jsonList(declarationViolations));
            fields.put(
                    "declaration_violation_count", String.valueOf(declarationViolations.size()));
            fields.put("blocking", String.valueOf(blocking()));
            StringBuilder out = new StringBuilder("{");
            boolean first = true;
            for (Map.Entry<String, String> e : fields.entrySet()) {
                if (!first) {
                    out.append(", ");
                }
                first = false;
                out.append(q(e.getKey())).append(": ").append(e.getValue());
            }
            return out.append('}').toString();
        }
    }

    /**
     * 検査できなかったファイルの結果。
     *
     * <p><b>読めなかったことを「矛盾なし」にしない。</b> ここを成功に倒すと、
     * 壊れたファイルが検査をすり抜ける。
     */
    private static Result failed(File file, Throwable exc) {
        // **全文は標準エラーへ。** JSON には要約だけを載せる。
        System.err.println("検査できませんでした: " + file.getPath());
        exc.printStackTrace(System.err);

        String message = exc.getClass().getSimpleName() + ": " + exc.getMessage();
        if (message.length() > MAX_ERROR_CHARS) {
            message = message.substring(0, MAX_ERROR_CHARS) + "…(標準エラーに全文あり)";
        }
        return new Result(
                file.getPath(),
                false,
                message,
                0,
                false,
                false,
                null,
                false,
                List.of(),
                List.of(),
                List.of());
    }

    /**
     * ELK が「この結論は不完全かもしれない」と言った理由を集める。
     *
     * <p>ELK は理由を {@code logStatus(Logger)} 経由でしか出さないので、
     * 受け皿の {@link CapturingLogger} を渡して回収する。
     */
    private static List<String> collectReasons(IncompletenessMonitor... monitors) {
        Set<String> reasons = new LinkedHashSet<>();
        for (IncompletenessMonitor monitor : monitors) {
            if (monitor == null || !monitor.isIncompletenessDetected()) {
                continue;
            }
            CapturingLogger sink = new CapturingLogger();
            monitor.logStatus(sink);
            reasons.addAll(sink.messages());
        }
        return new ArrayList<>(reasons);
    }

    private static Result check(File file) {
        OWLOntologyManager manager = OWLManager.createOWLOntologyManager();
        OWLOntology ontology;
        try {
            ontology = manager.loadOntologyFromOntologyDocument(file);
        } catch (Exception | LinkageError exc) {
            // `LinkageError` も拾うのは、依存関係の不整合が実際に
            // `NoSuchFieldError` として parse 中に出たため(ADR-0021 決定4)。
            // `Exception` だけでは素通りしてプロセスが落ち、JSON が出ない。
            return failed(file, exc);
        }

        // OWL 2 EL プロファイルの逸脱。**2 種類に分ける。**
        //   - 宣言の欠落 (`UseOfUndeclared*`) — SKOS や SHACL の語彙を宣言せずに
        //     使うと大量に出る。**推論の結論には影響しない**(OWLAPI は未宣言でも
        //     公理として扱う)。同梱サンプルで 25 件出る
        //   - それ以外 — 表現力に関わる逸脱。こちらは読み手にとって意味がある
        //
        // **分けないと、意味のある逸脱が宣言漏れの山に埋もれる**(実測で確認)。
        List<String> undeclared = new ArrayList<>();
        List<String> expressivity = new ArrayList<>();
        OWLProfileReport report = new OWL2ELProfile().checkOntology(ontology);
        for (OWLProfileViolation violation : report.getViolations()) {
            String type = violation.getClass().getSimpleName();
            if (type.startsWith(UNDECLARED_PREFIX) || type.equals(UNDECLARED_LEGACY)) {
                undeclared.add(violation.toString());
            } else {
                expressivity.add(violation.toString());
            }
        }
        // 出力を安定させる(同じ入力なら同じ順序)。
        List<String> declarationViolations = new ArrayList<>(new TreeSet<>(undeclared));
        List<String> expressivityViolations = new ArrayList<>(new TreeSet<>(expressivity));

        boolean inconsistencyFound;
        boolean consistencyConclusive;
        boolean unsatisfiableConclusive;
        List<String> unsatisfiableClasses;
        List<String> reasons;

        ElkReasoner reasoner = new ElkReasonerFactory().createReasoner(ontology);
        try {
            IncompleteResult<Boolean> consistency = reasoner.checkIsConsistent();
            IncompletenessMonitor consistencyMonitor = consistency.getIncompletenessMonitor();
            inconsistencyFound = !Incompleteness.getValue(consistency);
            consistencyConclusive = !consistencyMonitor.isIncompletenessDetected();

            IncompletenessMonitor unsatisfiableMonitor = null;
            if (inconsistencyFound) {
                // **矛盾しているオントロジーでは全クラスが充足不能になる**ので
                // 列挙しない(ELK 自身も `InconsistentOntologyException` を投げる)。
                // ここで `[]` を返すと「充足不能クラスは無かった」と読めてしまう
                // ので、**測っていないことを `null` で見せる**。
                unsatisfiableClasses = null;
            } else {
                IncompleteResult<Node<OWLClass>> unsatisfiable =
                        reasoner.computeUnsatisfiableClasses();
                unsatisfiableMonitor = unsatisfiable.getIncompletenessMonitor();
                // 出力を安定させるため TreeSet を経由する。
                TreeSet<String> iris = new TreeSet<>();
                for (OWLClass cls :
                        Incompleteness.getValue(unsatisfiable).getEntitiesMinusBottom()) {
                    iris.add(cls.getIRI().toString());
                }
                unsatisfiableClasses = new ArrayList<>(iris);
            }
            unsatisfiableConclusive =
                    unsatisfiableMonitor != null
                            && !unsatisfiableMonitor.isIncompletenessDetected();
            reasons = collectReasons(consistencyMonitor, unsatisfiableMonitor);
        } catch (Exception | LinkageError exc) {
            return failed(file, exc);
        } finally {
            reasoner.dispose();
        }

        return new Result(
                file.getPath(),
                true,
                null,
                ontology.getAxiomCount(),
                inconsistencyFound,
                consistencyConclusive,
                unsatisfiableClasses,
                unsatisfiableConclusive,
                reasons,
                expressivityViolations,
                declarationViolations);
    }

    public static void main(String[] args) {
        if (args.length == 0) {
            System.err.println(
                    "使い方: java -jar reasoner-check.jar <ontology.ttl> [<ontology.ttl> ...]");
            System.exit(2);
        }

        List<Result> results = new ArrayList<>();
        for (String path : args) {
            results.add(check(new File(path)));
        }

        boolean blocking = results.stream().anyMatch(Result::blocking);
        StringBuilder out = new StringBuilder("{\"blocking\": ")
                .append(blocking)
                .append(", \"reasoner\": ")
                .append(qOrNull(reasonerVersion()))
                .append(", \"profile\": \"OWL 2 EL\", \"results\": [");
        for (int i = 0; i < results.size(); i++) {
            if (i > 0) {
                out.append(", ");
            }
            out.append(results.get(i).toJson());
        }
        out.append("]}");
        System.out.println(out);

        // **終了コードで結果を伝える。** CI がこれで落ちる。
        System.exit(blocking ? 1 : 0);
    }
}
