// Простой HTTP-сервер на JDK (без внешних библиотек) со справочником
// 200 тыс. записей в куче — одинаковым у всех экземпляров.
import com.sun.net.httpserver.HttpServer;
import java.io.OutputStream;
import java.net.InetSocketAddress;
import java.nio.charset.StandardCharsets;
import java.util.HashMap;
import java.util.Map;

public class Server {
    static final Map<String, String> TABLE = new HashMap<>();

    public static void main(String[] args) throws Exception {
        for (int i = 0; i < 200_000; i++) TABLE.put("key-" + i, "value-" + i);
        HttpServer s = HttpServer.create(new InetSocketAddress(8000), 0);
        s.createContext("/", ex -> {
            byte[] b = ("{\"n\":" + TABLE.size() + "}").getBytes(StandardCharsets.UTF_8);
            ex.sendResponseHeaders(200, b.length);
            try (OutputStream o = ex.getResponseBody()) { o.write(b); }
        });
        s.start();
        System.out.println("listening 8000");
    }
}
