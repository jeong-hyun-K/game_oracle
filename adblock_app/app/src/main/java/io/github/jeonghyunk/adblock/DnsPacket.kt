package io.github.jeonghyunk.adblock

/**
 * VPN 터널로 들어온 IPv4/UDP DNS 패킷을 해석하고, 응답 패킷을 만듭니다.
 *
 * 앱이 "ads.example.com 주소가 뭐야?"라고 물으면(DNS 질의),
 *  - 광고 서버면 0.0.0.0(존재하지 않는 주소)으로 바로 답하고
 *  - 아니면 실제 DNS 서버에 물어본 답을 그대로 돌려줍니다.
 */
object DnsPacket {
    private const val IP_HEADER_MIN = 20
    private const val UDP_HEADER = 8
    private const val DNS_HEADER = 12
    private const val PROTO_UDP = 17

    const val TYPE_A = 1
    const val TYPE_AAAA = 28

    class Query(
        val srcIp: ByteArray,
        val dstIp: ByteArray,
        val srcPort: Int,
        val dstPort: Int,
        /** DNS 메시지 원본 (UDP 페이로드) */
        val dns: ByteArray,
        val domain: String,
        val qtype: Int,
        /** dns 배열에서 질문 영역이 끝나는 위치 */
        val questionEnd: Int,
    )

    /** 터널에서 읽은 패킷이 DNS 질의면 해석 결과를, 아니면 null 을 돌려줍니다. */
    fun parse(packet: ByteArray, length: Int): Query? {
        if (length < IP_HEADER_MIN + UDP_HEADER + DNS_HEADER) return null
        if ((packet[0].toInt() ushr 4) != 4) return null              // IPv4 만 처리
        val ihl = (packet[0].toInt() and 0x0F) * 4
        if (ihl < IP_HEADER_MIN) return null
        if (packet[9].toInt() and 0xFF != PROTO_UDP) return null
        val fragment = u16(packet, 6) and 0x3FFF                       // 조각난 패킷은 무시
        if (fragment != 0) return null
        val totalLen = minOf(u16(packet, 2), length)

        val udp = ihl
        if (totalLen < udp + UDP_HEADER + DNS_HEADER) return null
        val srcPort = u16(packet, udp)
        val dstPort = u16(packet, udp + 2)
        if (dstPort != 53) return null
        val udpLen = u16(packet, udp + 4)
        val dnsStart = udp + UDP_HEADER
        val dnsEnd = minOf(totalLen, udp + udpLen)
        if (dnsEnd - dnsStart < DNS_HEADER) return null
        val dns = packet.copyOfRange(dnsStart, dnsEnd)

        // 질의(QR=0)이고 질문이 1개 이상이어야 함
        if (dns[2].toInt() and 0x80 != 0) return null
        if (u16(dns, 4) < 1) return null

        val name = StringBuilder()
        var pos = DNS_HEADER
        while (true) {
            if (pos >= dns.size) return null
            val len = dns[pos].toInt() and 0xFF
            if (len == 0) { pos++; break }
            if (len and 0xC0 != 0) return null                          // 질의에 압축 포인터는 없음
            if (pos + 1 + len > dns.size || name.length + len > 253) return null
            if (name.isNotEmpty()) name.append('.')
            for (i in 1..len) name.append((dns[pos + i].toInt() and 0xFF).toChar())
            pos += 1 + len
        }
        if (pos + 4 > dns.size) return null
        val qtype = u16(dns, pos)
        pos += 4                                                        // QTYPE + QCLASS

        return Query(
            srcIp = packet.copyOfRange(12, 16),
            dstIp = packet.copyOfRange(16, 20),
            srcPort = srcPort,
            dstPort = dstPort,
            dns = dns,
            domain = name.toString().lowercase(),
            qtype = qtype,
            questionEnd = pos,
        )
    }

    /** 광고 서버 질의에 대한 응답: A 는 0.0.0.0, AAAA 는 ::, 그 외에는 빈 응답. */
    fun blockedAnswer(q: Query): ByteArray {
        val question = q.dns.copyOfRange(DNS_HEADER, q.questionEnd)
        val answer: ByteArray = when (q.qtype) {
            TYPE_A -> record(TYPE_A, ByteArray(4))
            TYPE_AAAA -> record(TYPE_AAAA, ByteArray(16))
            else -> ByteArray(0)
        }
        val out = ByteArray(DNS_HEADER + question.size + answer.size)
        out[0] = q.dns[0]                                              // 질의 ID 그대로
        out[1] = q.dns[1]
        out[2] = (0x80 or (q.dns[2].toInt() and 0x79)).toByte()        // QR=1, OPCODE·RD 유지
        out[3] = 0x80.toByte()                                         // RA=1, 오류 없음
        put16(out, 4, 1)                                               // 질문 1개
        put16(out, 6, if (answer.isEmpty()) 0 else 1)                  // 답 0~1개
        question.copyInto(out, DNS_HEADER)
        answer.copyInto(out, DNS_HEADER + question.size)
        return out
    }

    private fun record(type: Int, addr: ByteArray): ByteArray {
        val r = ByteArray(12 + addr.size)
        r[0] = 0xC0.toByte(); r[1] = 0x0C                               // 질문의 이름을 가리키는 포인터
        put16(r, 2, type)
        put16(r, 4, 1)                                                 // CLASS IN
        put16(r, 6, 0); put16(r, 8, 60)                                // TTL 60초
        put16(r, 10, addr.size)
        addr.copyInto(r, 12)
        return r
    }

    /** DNS 응답을 원래 질의를 보낸 앱에게 돌려줄 IPv4/UDP 패킷으로 감쌉니다. */
    fun wrapResponse(q: Query, dnsPayload: ByteArray): ByteArray {
        val udpLen = UDP_HEADER + dnsPayload.size
        val total = IP_HEADER_MIN + udpLen
        val p = ByteArray(total)
        // IP 헤더 (보낸 쪽과 받는 쪽을 뒤바꿈)
        p[0] = 0x45
        put16(p, 2, total)
        p[6] = 0x40                                                    // Don't Fragment
        p[8] = 64                                                      // TTL
        p[9] = PROTO_UDP.toByte()
        q.dstIp.copyInto(p, 12)
        q.srcIp.copyInto(p, 16)
        put16(p, 10, checksum(p, 0, IP_HEADER_MIN, 0))
        // UDP 헤더
        put16(p, 20, q.dstPort)
        put16(p, 22, q.srcPort)
        put16(p, 24, udpLen)
        dnsPayload.copyInto(p, 28)
        val pseudo = sum(p, 12, 8) + PROTO_UDP + udpLen
        var udpSum = checksum(p, 20, udpLen, pseudo)
        if (udpSum == 0) udpSum = 0xFFFF
        put16(p, 26, udpSum)
        return p
    }

    private fun sum(b: ByteArray, off: Int, len: Int): Long {
        var s = 0L
        var i = off
        while (i + 1 < off + len) { s += u16(b, i); i += 2 }
        if (i < off + len) s += (b[i].toInt() and 0xFF) shl 8
        return s
    }

    internal fun checksum(b: ByteArray, off: Int, len: Int, initial: Long): Int {
        var s = sum(b, off, len) + initial
        while (s ushr 16 != 0L) s = (s and 0xFFFF) + (s ushr 16)
        return (s.inv() and 0xFFFF).toInt()
    }

    internal fun u16(b: ByteArray, off: Int) =
        ((b[off].toInt() and 0xFF) shl 8) or (b[off + 1].toInt() and 0xFF)

    private fun put16(b: ByteArray, off: Int, v: Int) {
        b[off] = (v ushr 8).toByte()
        b[off + 1] = v.toByte()
    }
}
