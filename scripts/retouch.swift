// 제품 실사진 보정 + 누끼 엔진 (맥 내장 Core Image·Vision만 씀, 생성형 아님)
//  - 글씨를 다시 그리지 않는다: 밝기·색·대비·선명도 같은 "사진 전체에 똑같이 거는" 보정만 한다.
//  - 원본 조각을 덧붙이지 않는다: 누끼·흰 배경본은 전부 보정본 한 장에서 잘라 만든다.
//  - 글씨 확인: 원본·보정본·흰 배경본에서 글자를 읽어(OCR) 똑같이 읽히는지 비교해 보고서에 남긴다.
// 사용법: retouch <입력 사진> <결과 폴더> [--no-shadow] [--center] [--no-adjust]
//   결과: 보정.jpg, 누끼.png, 흰배경.jpg, 미리보기(-web.jpg), report.json
import Foundation
import Vision
import CoreImage
import CoreImage.CIFilterBuiltins
import AppKit
import UniformTypeIdentifiers

let t0 = Date()
let args = CommandLine.arguments
guard args.count >= 3 else { print("사용법: retouch <입력> <결과폴더> [--no-shadow] [--center]"); exit(1) }
let inURL = URL(fileURLWithPath: args[1])
let outDir = URL(fileURLWithPath: args[2], isDirectory: true)
let useShadow = !args.contains("--no-shadow")
let centerOnly = args.contains("--center")
let noAdjust = args.contains("--no-adjust")   // 이미 보정된 사진(AI 보정본)에서 누끼만 딸 때
try? FileManager.default.createDirectory(at: outDir, withIntermediateDirectories: true)

let sRGB = CGColorSpace(name: CGColorSpace.sRGB)!
// 작업 색공간을 sRGB로: 아래 통계(sRGB 값)와 필터 계산이 같은 기준이 되게
let ctx = CIContext(options: [.workingColorSpace: sRGB, .outputColorSpace: sRGB, .cacheIntermediates: false])

var report: [String: Any] = ["input": inURL.lastPathComponent]
func fail(_ msg: String) -> Never {
    report["error"] = msg
    if let d = try? JSONSerialization.data(withJSONObject: report, options: [.prettyPrinted]) { try? d.write(to: outDir.appendingPathComponent("report.json")) }
    print("실패: \(msg)"); exit(2)
}

// ---------- 1. 불러오기 (사진 방향 반영, 투명 PNG면 흰 바탕에 올려 사진처럼) ----------
guard var src = CIImage(contentsOf: inURL, options: [.applyOrientationProperty: true]) else { fail("사진을 열 수 없어요") }
src = src.transformed(by: CGAffineTransform(translationX: -src.extent.minX, y: -src.extent.minY))
src = src.composited(over: CIImage(color: .white).cropped(to: src.extent))
let W = Int(src.extent.width), H = Int(src.extent.height)
let full = CGRect(x: 0, y: 0, width: W, height: H)
report["size"] = [W, H]

// ---------- 2. 제품 영역 찾기 (Vision 전경 분리) ----------
let vreq = VNGenerateForegroundInstanceMaskRequest()
let vh = VNImageRequestHandler(ciImage: src)
do { try vh.perform([vreq]) } catch { fail("제품 영역을 찾지 못했어요: \(error.localizedDescription)") }
guard let obs = vreq.results?.first, !obs.allInstances.isEmpty else { fail("사진에서 제품을 찾지 못했어요") }
var pick = obs.allInstances
if centerOnly {
    let inst = obs.instanceMask
    CVPixelBufferLockBaseAddress(inst, .readOnly)
    let mw = CVPixelBufferGetWidth(inst), mh = CVPixelBufferGetHeight(inst), rb = CVPixelBufferGetBytesPerRow(inst)
    let p = CVPixelBufferGetBaseAddress(inst)!.assumingMemoryBound(to: UInt8.self)
    let l = Int(p[(mh / 2) * rb + mw / 2])
    CVPixelBufferUnlockBaseAddress(inst, .readOnly)
    if l != 0 { pick = IndexSet(integer: l) }
}
report["objects"] = pick.count
guard let maskBuf = try? obs.generateScaledMaskForImage(forInstances: pick, from: vh) else { fail("누끼 마스크를 만들지 못했어요") }
var mask = CIImage(cvPixelBuffer: maskBuf)
mask = mask.transformed(by: CGAffineTransform(scaleX: CGFloat(W) / mask.extent.width, y: CGFloat(H) / mask.extent.height))
// 마스크가 제품보다 살짝 넓게 잡혀 테두리에 배경이 비치므로(밝은 띠 = 합성티) 조금 안쪽으로 줄인 뒤 아주 살짝 부드럽게
let erode = max(1.0, Double(max(W, H)) / 1400)
mask = mask.clampedToExtent().applyingFilter("CIMorphologyMinimum", parameters: ["inputRadius": erode])
    .applyingFilter("CIGaussianBlur", parameters: ["inputRadius": max(0.6, erode * 0.4)]).cropped(to: full)
// 경계 살짝 단단하게 (배경이 비쳐 보이는 반투명 테두리 줄이기): a' = (a-0.08)/0.84
mask = mask.applyingFilter("CIColorMatrix", parameters: [
    "inputRVector": CIVector(x: 1/0.84, y: 0, z: 0, w: 0), "inputGVector": CIVector(x: 1/0.84, y: 0, z: 0, w: 0),
    "inputBVector": CIVector(x: 1/0.84, y: 0, z: 0, w: 0), "inputAVector": CIVector(x: 0, y: 0, z: 0, w: 1),
    "inputBiasVector": CIVector(x: -0.08/0.84, y: -0.08/0.84, z: -0.08/0.84, w: 0)]).applyingFilter("CIColorClamp").cropped(to: full)

// ---------- 3. 분석 (작게 줄여 통계) ----------
func render8(_ img: CIImage, _ w: Int, _ h: Int) -> [UInt8] {
    var buf = [UInt8](repeating: 0, count: w * h * 4)
    let s = img.transformed(by: CGAffineTransform(scaleX: CGFloat(w) / img.extent.width, y: CGFloat(h) / img.extent.height))
    ctx.render(s, toBitmap: &buf, rowBytes: w * 4, bounds: CGRect(x: 0, y: 0, width: w, height: h), format: .RGBA8, colorSpace: sRGB)
    return buf
}
let scale = min(1.0, 640.0 / Double(max(W, H)))
let aw = max(1, Int(Double(W) * scale)), ah = max(1, Int(Double(H) * scale))
let A = render8(src, aw, ah), M = render8(mask, aw, ah)
func lum(_ r: Double, _ g: Double, _ b: Double) -> Double { 0.2126 * r + 0.7152 * g + 0.0722 * b }

var bgSum = [0.0, 0.0, 0.0], bgN = 0.0, bgSat = [Double]()
var fgN = 0.0
for i in 0..<(aw * ah) {
    let m = Double(M[i * 4]) / 255
    let r = Double(A[i * 4]) / 255, g = Double(A[i * 4 + 1]) / 255, b = Double(A[i * 4 + 2]) / 255
    if m < 0.05 {
        bgSum[0] += r; bgSum[1] += g; bgSum[2] += b; bgN += 1
        let mx = max(r, g, b), mn = min(r, g, b); bgSat.append(mx > 0.02 ? (mx - mn) / mx : 0)
    } else if m > 0.5 { fgN += 1 }
}
let total = Double(aw * ah)
let bgFrac = bgN / total
report["productArea"] = (fgN / total * 1000).rounded() / 1000
if fgN / total < 0.01 { fail("제품이 너무 작게 잡혔어요 (사진에서 제품을 크게 찍어 주세요)") }

// 3-1. 화이트밸런스: 배경이 흰·회색 종이처럼 무채색에 가까우면 그 배경을 기준으로 색 틀어짐을 바로잡는다
var gains = [1.0, 1.0, 1.0], wbMode = "그대로"
bgSat.sort()
let bgSatMed = bgSat.isEmpty ? 1 : bgSat[bgSat.count / 2]
if bgN > 0 {
    let bm = bgSum.map { $0 / bgN }
    let bl = lum(bm[0], bm[1], bm[2])
    if bgFrac > 0.12 && bl > 0.25 && bgSatMed < 0.45 {
        // 채도 0.30 이하면 완전히, 0.45까지는 점점 약하게 (일부러 고른 색 배경은 덜 건드림)
        let k = min(1, max(0, (0.45 - bgSatMed) / 0.15))
        let gray = (bm[0] + bm[1] + bm[2]) / 3
        gains = bm.map { 1 + k * (min(1.30, max(0.78, gray / max($0, 0.01))) - 1) }; wbMode = k >= 0.99 ? "배경 기준" : "배경 기준(약하게)"
    } else {
        // 배경이 색 있는 경우: 사진 전체 평균으로 아주 약하게만
        var s = [0.0, 0.0, 0.0]
        for i in 0..<(aw * ah) { for c in 0..<3 { s[c] += Double(A[i * 4 + c]) / 255 } }
        let mm = s.map { $0 / total }, gray = (mm[0] + mm[1] + mm[2]) / 3
        gains = mm.map { 1 + 0.4 * (min(1.08, max(0.93, gray / max($0, 0.01))) - 1) }; wbMode = "전체 평균(약하게)"
    }
}

// 3-2. 밝기 범위(레벨): 가장 어두운 0.3%·가장 밝은 0.3% 기준으로 펴기. 배경이 밝은 무채색이면 배경을 거의 흰색으로
var L = [Double](), fgL = [Double]()
L.reserveCapacity(aw * ah)
for i in 0..<(aw * ah) {
    let r = Double(A[i * 4]) / 255 * gains[0], g = Double(A[i * 4 + 1]) / 255 * gains[1], b = Double(A[i * 4 + 2]) / 255 * gains[2]
    let l = min(1, lum(r, g, b)); L.append(l)
    if M[i * 4] > 128 { fgL.append(l) }
}
var Ls = L; Ls.sort(); fgL.sort()
func pct(_ a: [Double], _ p: Double) -> Double { a.isEmpty ? 0 : a[min(a.count - 1, Int(Double(a.count) * p))] }
var black = min(0.06, pct(Ls, 0.003))
var white = min(1.0, max(0.80, pct(Ls, 0.997)))
if wbMode.hasPrefix("배경 기준") && bgN > 0 {
    var bl = [Double]()
    for i in 0..<(aw * ah) where M[i * 4] < 13 { bl.append(L[i]) }
    bl.sort()
    let bgMed = pct(bl, 0.5)
    if bgMed > 0.55 { white = min(white, max(0.72, bgMed / 0.97)) }   // 배경 중간 밝기 → 약 97% 흰색
}
// 3-3. 제품 중간 밝기: 너무 어두우면 감마로 살짝 올림
let fgMid = (pct(fgL, 0.5) - black) / max(0.05, white - black)
var gamma = 1.0
if fgMid < 0.42 && fgMid > 0.03 { gamma = max(0.72, log(0.45) / log(fgMid)) }
report["adjust"] = ["whiteBalance": wbMode, "bgSaturation": (bgSatMed * 100).rounded() / 100, "gains": gains.map { ($0 * 1000).rounded() / 1000 },
                    "black": (black * 1000).rounded() / 1000, "white": (white * 1000).rounded() / 1000,
                    "gamma": (gamma * 1000).rounded() / 1000, "productMid": (fgMid * 1000).rounded() / 1000]

// ---------- 4. 보정 (전부 사진 전체에 같은 공식 → 글자 모양은 그대로) ----------
let span = white - black
var img = src.applyingFilter("CIColorMatrix", parameters: [
    "inputRVector": CIVector(x: CGFloat(gains[0] / span), y: 0, z: 0, w: 0),
    "inputGVector": CIVector(x: 0, y: CGFloat(gains[1] / span), z: 0, w: 0),
    "inputBVector": CIVector(x: 0, y: 0, z: CGFloat(gains[2] / span), w: 0),
    "inputAVector": CIVector(x: 0, y: 0, z: 0, w: 1),
    "inputBiasVector": CIVector(x: CGFloat(-black / span), y: CGFloat(-black / span), z: CGFloat(-black / span), w: 0)])
    .applyingFilter("CIColorClamp")
if gamma < 0.999 { img = img.applyingFilter("CIGammaAdjust", parameters: ["inputPower": gamma]) }
img = img.applyingFilter("CIHighlightShadowAdjust", parameters: ["inputShadowAmount": 0.18, "inputHighlightAmount": 0.95])
img = img.applyingFilter("CIVibrance", parameters: ["inputAmount": 0.12])
// 잡티(노이즈)는 아주 약하게만 — 세게 하면 작은 글씨가 뭉개진다
img = img.applyingFilter("CINoiseReduction", parameters: ["inputNoiseLevel": 0.006, "inputSharpness": 0.5])
// 선명도: 밝기만 선명하게(색 번짐 없음), 반경을 사진 크기에 맞춤
let sharpR = max(0.8, min(2.0, Double(max(W, H)) / 2400))
img = img.applyingFilter("CISharpenLuminance", parameters: ["inputSharpness": 0.35, "inputRadius": sharpR])
if noAdjust { img = src }
img = img.cropped(to: full)

// ---------- 5. 결과 저장 ----------
func writeJPG(_ im: CIImage, _ name: String, _ q: Double = 0.95) {
    let u = outDir.appendingPathComponent(name)
    try? ctx.writeJPEGRepresentation(of: im, to: u, colorSpace: sRGB, options: [kCGImageDestinationLossyCompressionQuality as CIImageRepresentationOption: q])
}
func web(_ im: CIImage, _ maxSide: Double = 1600) -> CIImage {
    let s = min(1.0, maxSide / Double(max(im.extent.width, im.extent.height)))
    return s < 1 ? im.applyingFilter("CILanczosScaleTransform", parameters: ["inputScale": s, "inputAspectRatio": 1]) : im
}
writeJPG(img, "보정.jpg")
writeJPG(web(src), "원본-web.jpg", 0.85)
writeJPG(web(img), "보정-web.jpg", 0.85)

// 5-1. 누끼: 보정본 + 마스크. 경계 색 정리(예전 배경색이 테두리에 묻어나는 것 제거) → 합성티 줄이기
var Cbuf = [UInt8](repeating: 0, count: W * H * 4)
var Mbuf = [UInt8](repeating: 0, count: W * H * 4)
ctx.render(img, toBitmap: &Cbuf, rowBytes: W * 4, bounds: full, format: .RGBA8, colorSpace: sRGB)
ctx.render(mask, toBitmap: &Mbuf, rowBytes: W * 4, bounds: full, format: .RGBA8, colorSpace: sRGB)
// 주변 배경색 지도: (배경만 남긴 사진을 흐림) ÷ (배경 마스크를 흐림)
let inv = mask.applyingFilter("CIColorInvert")
let blurR = Double(max(W, H)) / 120
let bgOnly = img.applyingFilter("CIBlendWithMask", parameters: ["inputBackgroundImage": CIImage(color: .black).cropped(to: full), "inputMaskImage": inv])
let bgBlur = bgOnly.clampedToExtent().applyingFilter("CIGaussianBlur", parameters: ["inputRadius": blurR]).cropped(to: full)
let invBlur = inv.clampedToExtent().applyingFilter("CIGaussianBlur", parameters: ["inputRadius": blurR]).cropped(to: full)
var Bbuf = [UInt8](repeating: 0, count: W * H * 4), Ibuf = [UInt8](repeating: 0, count: W * H * 4)
ctx.render(bgBlur, toBitmap: &Bbuf, rowBytes: W * 4, bounds: full, format: .RGBA8, colorSpace: sRGB)
ctx.render(invBlur, toBitmap: &Ibuf, rowBytes: W * 4, bounds: full, format: .RGBA8, colorSpace: sRGB)
// 마스크 테두리 상자
var x0 = W, y0 = H, x1 = -1, y1 = -1
var edgeFixed = 0
var out = [UInt8](repeating: 0, count: W * H * 4)
for y in 0..<H {
    for x in 0..<W {
        let i = (y * W + x) * 4
        let a = Int(Mbuf[i])
        if a < 3 { continue }
        if x < x0 { x0 = x }; if x > x1 { x1 = x }; if y < y0 { y0 = y }; if y > y1 { y1 = y }
        if a > 250 {
            out[i] = Cbuf[i]; out[i + 1] = Cbuf[i + 1]; out[i + 2] = Cbuf[i + 2]; out[i + 3] = 255; continue
        }
        let af = Double(a) / 255, iv = max(Double(Ibuf[i]) / 255, 0.02)
        for c in 0..<3 {
            let C = Double(Cbuf[i + c]), B = min(255, Double(Bbuf[i + c]) / iv)
            let F = (C - (1 - af) * B) / max(af, 0.25)
            out[i + c] = UInt8(max(0, min(255, F.rounded())))
        }
        out[i + 3] = UInt8(a); edgeFixed += 1
    }
}
if x1 < 0 { fail("누끼 영역이 비었어요") }
let pad = Int(Double(max(x1 - x0, y1 - y0)) * 0.02)
let cx0 = max(0, x0 - pad), cy0 = max(0, y0 - pad), cx1 = min(W - 1, x1 + pad), cy1 = min(H - 1, y1 + pad)
let cw = cx1 - cx0 + 1, ch = cy1 - cy0 + 1
var crop = [UInt8](repeating: 0, count: cw * ch * 4)
for y in 0..<ch { let s = ((y + cy0) * W + cx0) * 4; let d = y * cw * 4; crop[d..<(d + cw * 4)] = out[s..<(s + cw * 4)] }
let provider = CGDataProvider(data: Data(crop) as CFData)!
let cut = CGImage(width: cw, height: ch, bitsPerComponent: 8, bitsPerPixel: 32, bytesPerRow: cw * 4, space: sRGB,
                  bitmapInfo: CGBitmapInfo(rawValue: CGImageAlphaInfo.last.rawValue), provider: provider, decode: nil, shouldInterpolate: true, intent: .defaultIntent)!
let cutURL = outDir.appendingPathComponent("누끼.png")
if let dst = CGImageDestinationCreateWithURL(cutURL as CFURL, UTType.png.identifier as CFString, 1, nil) { CGImageDestinationAddImage(dst, cut, nil); CGImageDestinationFinalize(dst) }
report["cutout"] = ["size": [cw, ch], "edgePixels": edgeFixed]

// 5-2. 흰 배경본 (정사각, 제품 80%) + 부드러운 바닥 그림자 (제품 모양에서 만든 것, 원본 조각 아님)
let cutCI = CIImage(cgImage: cut)
let side = Double(max(cw, ch)) / 0.80
let S = CGFloat(side.rounded())
let ox = (S - CGFloat(cw)) / 2, oy = (S - CGFloat(ch)) / 2
let canvas = CGRect(x: 0, y: 0, width: S, height: S)
var comp = CIImage(color: .white).cropped(to: canvas)
if useShadow {
    // 제품 바닥 줄(마스크 맨 아래 8%)만 납작하게 눌러 그림자로
    let alphaOnly = cutCI.applyingFilter("CIColorMatrix", parameters: [
        "inputRVector": CIVector(x: 0, y: 0, z: 0, w: 0), "inputGVector": CIVector(x: 0, y: 0, z: 0, w: 0),
        "inputBVector": CIVector(x: 0, y: 0, z: 0, w: 0), "inputAVector": CIVector(x: 0, y: 0, z: 0, w: 1)])
    let base = alphaOnly.cropped(to: CGRect(x: 0, y: 0, width: CGFloat(cw), height: CGFloat(ch) * 0.08))
    func shadow(_ squash: CGFloat, _ blur: Double, _ op: CGFloat) -> CIImage {
        base.transformed(by: CGAffineTransform(scaleX: 1.02, y: squash).concatenating(CGAffineTransform(translationX: ox - CGFloat(cw) * 0.01, y: oy - CGFloat(ch) * 0.006)))
            .applyingFilter("CIColorMatrix", parameters: ["inputAVector": CIVector(x: 0, y: 0, z: 0, w: op)])
            .applyingFilter("CIGaussianBlur", parameters: ["inputRadius": blur]).cropped(to: canvas)
    }
    let soft = shadow(0.35, side * 0.012, 0.22), contact = shadow(0.12, side * 0.003, 0.35)
    comp = soft.composited(over: comp); comp = contact.composited(over: comp)
}
comp = cutCI.transformed(by: CGAffineTransform(translationX: ox, y: oy)).composited(over: comp).cropped(to: canvas)
writeJPG(comp, "흰배경.jpg")
writeJPG(web(comp), "흰배경-web.jpg", 0.85)
// 누끼 미리보기(투명 PNG 작은 것)
let cutWeb = web(cutCI, 1200)
try? ctx.writePNGRepresentation(of: cutWeb, to: outDir.appendingPathComponent("누끼-web.png"), format: .RGBA8, colorSpace: sRGB)

// ---------- 6. 글씨 확인 (OCR) ----------
struct Line { let text: String; let box: CGRect; let conf: Float }
func readText(_ im: CIImage) -> [Line] {
    let r = VNRecognizeTextRequest()
    r.recognitionLevel = .accurate; r.usesLanguageCorrection = false
    r.recognitionLanguages = ["ko-KR", "en-US"]; r.minimumTextHeight = 0
    let h = VNImageRequestHandler(ciImage: im)
    try? h.perform([r])
    return (r.results ?? []).compactMap { o in
        guard let c = o.topCandidates(1).first else { return nil }
        let t = c.string.trimmingCharacters(in: .whitespaces)
        return t.count >= 2 && c.confidence >= 0.4 ? Line(text: t, box: o.boundingBox, conf: c.confidence) : nil
    }
}
func norm(_ s: String) -> [Character] { Array(s.lowercased().filter { !$0.isWhitespace }) }
func sim(_ a: String, _ b: String) -> Double {
    let x = norm(a), y = norm(b)
    if x.isEmpty || y.isEmpty { return 0 }
    var d = Array(0...y.count)
    for i in 1...x.count {
        var prev = d[0]; d[0] = i
        for j in 1...y.count { let t = d[j]; d[j] = min(d[j] + 1, d[j - 1] + 1, prev + (x[i - 1] == y[j - 1] ? 0 : 1)); prev = t }
    }
    return 1 - Double(d[y.count]) / Double(max(x.count, y.count))
}
// 원본 글씨 중 제품 위에 있는 것만 (배경 글씨는 누끼에서 빠지는 게 맞으니까)
func onProduct(_ box: CGRect) -> Bool {
    let px = Int(box.midX * CGFloat(aw)), py = Int(box.midY * CGFloat(ah))   // Vision 좌표: 아래가 0
    let yy = ah - 1 - min(ah - 1, max(0, py)), xx = min(aw - 1, max(0, px))
    return M[(yy * aw + xx) * 4] > 128
}
let orig = readText(src).filter { onProduct($0.box) }
// 누끼 결과를 원본과 같은 자리·크기로 흰 바탕에 놓은 판 (글씨 비교용 — 크기가 달라 생기는 OCR 차이를 없앰)
let fullProv = CGDataProvider(data: Data(out) as CFData)!
let cutFullCG = CGImage(width: W, height: H, bitsPerComponent: 8, bitsPerPixel: 32, bytesPerRow: W * 4, space: sRGB,
                        bitmapInfo: CGBitmapInfo(rawValue: CGImageAlphaInfo.last.rawValue), provider: fullProv, decode: nil, shouldInterpolate: true, intent: .defaultIntent)!
let cutFull = CIImage(cgImage: cutFullCG).composited(over: CIImage(color: .white).cropped(to: full))
let afterRet = readText(img), afterCut = readText(cutFull)
// 긴 글 안에서 같은 길이 조각을 옮겨 가며 가장 비슷한 곳 찾기
func partial(_ needle: String, _ hay: [Character]) -> Double {
    let n = norm(needle)
    if n.isEmpty || hay.count < n.count { return 0 }
    var best = 0.0
    var i = 0
    while i + n.count <= hay.count {
        let v = sim(String(n), String(hay[i..<(i + n.count)]))
        if v > best { best = v; if best >= 0.99 { break } }
        i += 1
    }
    return best
}
// 그 줄 자리만 잘라 2배로 키워서 원본·결과를 똑같이 다시 읽어 비교 (기계 읽기 실수와 진짜 뭉개짐 구분)
func zoomRead(_ im: CIImage, _ box: CGRect) -> String {
    let r = CGRect(x: box.minX * CGFloat(W), y: box.minY * CGFloat(H), width: box.width * CGFloat(W), height: box.height * CGFloat(H))
        .insetBy(dx: -box.height * CGFloat(H) * 0.6, dy: -box.height * CGFloat(H) * 0.6).intersection(full)
    let c = im.cropped(to: r).transformed(by: CGAffineTransform(translationX: -r.minX, y: -r.minY)).applyingFilter("CILanczosScaleTransform", parameters: ["inputScale": 2.0, "inputAspectRatio": 1])
    return readText(c).map { $0.text }.joined()
}
// 글자 모양 그대로인지 픽셀로 직접 확인: 그 줄 자리의 밝기 무늬가 원본과 얼마나 같은지(상관계수, 1 = 똑같음).
// 밝기·색만 바꾼 보정은 0.97 이상이 나오고, 뭉개지거나 다시 그려진 글씨는 크게 떨어진다.
var Sbuf = [UInt8](repeating: 0, count: W * H * 4)
ctx.render(src, toBitmap: &Sbuf, rowBytes: W * 4, bounds: full, format: .RGBA8, colorSpace: sRGB)
func lumAt(_ b: [UInt8], _ i: Int, white: Bool) -> Double {
    var r = Double(b[i]), g = Double(b[i + 1]), bl = Double(b[i + 2])
    if white { let a = Double(b[i + 3]) / 255; r = r * a + 255 * (1 - a); g = g * a + 255 * (1 - a); bl = bl * a + 255 * (1 - a) }
    return 0.2126 * r + 0.7152 * g + 0.0722 * bl
}
func corr(_ box: CGRect, _ tb: [UInt8], white: Bool) -> Double {
    let x0 = max(0, Int(box.minX * CGFloat(W))), x1 = min(W - 1, Int(box.maxX * CGFloat(W)))
    let y0 = max(0, H - 1 - Int(box.maxY * CGFloat(H))), y1 = min(H - 1, H - 1 - Int(box.minY * CGFloat(H)))
    if x1 <= x0 || y1 <= y0 { return 0 }
    var n = 0.0, sa = 0.0, sb = 0.0, saa = 0.0, sbb = 0.0, sab = 0.0
    for y in y0...y1 { for x in x0...x1 {
        let i = (y * W + x) * 4
        let a = lumAt(Sbuf, i, white: false), b = lumAt(tb, i, white: white)
        n += 1; sa += a; sb += b; saa += a * a; sbb += b * b; sab += a * b
    } }
    let va = saa - sa * sa / n, vb = sbb - sb * sb / n
    if va < 1 || vb < 1 { return 0 }
    return (sab - sa * sb / n) / (va * vb).squareRoot()
}
func check(_ after: [Line], _ target: CIImage, _ tb: [UInt8], white: Bool) -> [[String: Any]] {
    let hay = norm(after.map { $0.text }.joined())
    return orig.map { o in
        var s = after.map { sim(o.text, $0.text) }.max() ?? 0
        if s < 0.8 { s = max(s, partial(o.text, hay)) }
        if s < 0.8 {   // 확대해서 원본·결과를 같은 방식으로 다시 읽어 비교
            let a = zoomRead(src, o.box), b = zoomRead(target, o.box)
            if !a.isEmpty { s = max(s, sim(a, b)) }
            if ProcessInfo.processInfo.environment["DEBUG_OCR"] != nil { print("[글씨]", o.text, "| 원본:", a, "| 결과:", b) }
        }
        let px = corr(o.box, tb, white: white)
        return ["text": o.text, "score": (s * 100).rounded() / 100, "pixel": (px * 1000).rounded() / 1000, "ok": s >= 0.8 || px >= 0.95]
    }
}
let cr = check(afterRet, img, Cbuf, white: false), cw2 = check(afterCut, cutFull, out, white: true)
let okR = cr.filter { $0["ok"] as! Bool }.count, okW = cw2.filter { $0["ok"] as! Bool }.count
report["text"] = ["found": orig.count, "keptRetouched": okR, "keptCutout": okW,
                  "lines": orig.indices.map { i in ["text": orig[i].text, "retouched": cr[i]["score"]!, "cutout": cw2[i]["score"]!,
                                                    "pixelRetouched": cr[i]["pixel"]!, "pixelCutout": cw2[i]["pixel"]!,
                                                    "ok": (cr[i]["ok"] as! Bool) && (cw2[i]["ok"] as! Bool)] }]
report["outputs"] = ["retouched": "보정.jpg", "cutout": "누끼.png", "white": "흰배경.jpg",
                     "previewOriginal": "원본-web.jpg", "previewRetouched": "보정-web.jpg", "previewWhite": "흰배경-web.jpg", "previewCutout": "누끼-web.png"]
report["seconds"] = (Date().timeIntervalSince(t0) * 10).rounded() / 10
if let d = try? JSONSerialization.data(withJSONObject: report, options: [.prettyPrinted, .sortedKeys]) { try? d.write(to: outDir.appendingPathComponent("report.json")) }
print("완료 \(report["seconds"]!)초 · 글씨 \(orig.count)줄 중 보정 \(okR) / 누끼 \(okW) 유지 · 누끼 \(cw)x\(ch)")
