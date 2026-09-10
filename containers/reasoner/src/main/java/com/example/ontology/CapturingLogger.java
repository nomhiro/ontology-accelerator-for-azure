package com.example.ontology;

import java.util.ArrayList;
import java.util.Collections;
import java.util.List;

import org.slf4j.Marker;
import org.slf4j.event.Level;
import org.slf4j.helpers.AbstractLogger;
import org.slf4j.helpers.MessageFormatter;

/**
 * ログを溜めるだけの SLF4J ロガー。
 *
 * <p><b>なぜこれが必要か。</b> ELK の {@code IncompletenessMonitor} は
 * 「不完全かどうか」は {@code boolean} で返すが、<b>その理由はログにしか
 * 出さない</b>({@code logStatus(Logger)})。理由まで JSON に載せたいので、
 * ELK にこのロガーを渡して回収する(ADR-0021 決定1)。
 *
 * <p><b>全レベルを有効と答える。</b> ELK は {@code isInfoEnabled()} を見て
 * 詳細行を出すかどうかを決めるため、ここで false を返すと<b>理由が
 * 「不完全です」の 1 行だけになる</b>。
 *
 * <p>標準エラーへの通常のログは slf4j-simple が別に担う
 * ({@code simplelogger.properties} で WARN 以上に絞ってある)。
 */
final class CapturingLogger extends AbstractLogger {

    private final List<String> messages = new ArrayList<>();

    CapturingLogger() {
        this.name = CapturingLogger.class.getName();
    }

    List<String> messages() {
        return Collections.unmodifiableList(messages);
    }

    @Override
    protected String getFullyQualifiedCallerName() {
        return null;
    }

    @Override
    protected void handleNormalizedLoggingCall(
            Level level, Marker marker, String message, Object[] arguments, Throwable throwable) {
        if (message == null) {
            return;
        }
        messages.add(MessageFormatter.basicArrayFormat(message, arguments));
    }

    @Override
    public boolean isTraceEnabled() {
        // TRACE と DEBUG は受け取らない。ELK は DEBUG で**逸脱した公理を 1 つずつ**
        // 出すため、CI の JSON が公理の山で埋まる。理由の種類が分かれば足りる。
        return false;
    }

    @Override
    public boolean isTraceEnabled(Marker marker) {
        return isTraceEnabled();
    }

    @Override
    public boolean isDebugEnabled() {
        return false;
    }

    @Override
    public boolean isDebugEnabled(Marker marker) {
        return isDebugEnabled();
    }

    @Override
    public boolean isInfoEnabled() {
        return true;
    }

    @Override
    public boolean isInfoEnabled(Marker marker) {
        return isInfoEnabled();
    }

    @Override
    public boolean isWarnEnabled() {
        return true;
    }

    @Override
    public boolean isWarnEnabled(Marker marker) {
        return isWarnEnabled();
    }

    @Override
    public boolean isErrorEnabled() {
        return true;
    }

    @Override
    public boolean isErrorEnabled(Marker marker) {
        return isErrorEnabled();
    }
}
