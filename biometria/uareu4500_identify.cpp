/*
 * uareu4500_identify.cpp
 * Adiciona funcao python_identify_finger que:
 * 1. Captura o dedo UMA vez
 * 2. Compara contra array de FMDs em memoria (1:N)
 * 3. Retorna o indice do match ou -1
 *
 * Compile com MinGW:
 * g++ -shared -o uareu4500_identify.dll uareu4500_identify.cpp
 *   -I"C:\Program Files (x86)\DigitalPersona\U.are.U SDK\Include"
 *   -L"C:\Academia_teste\biometria"
 *   -ldpfpdd -ldpfj
 *   -std=c++11 -static-libgcc -static-libstdc++
 */

#include <iostream>
#include <vector>
#include <string>
#include <cstring>
#include <openssl/bio.h>
#include <openssl/evp.h>
#include <openssl/buffer.h>

// Inclui headers do SDK
extern "C" {
#include "dpfpdd.h"
#include "dpfj.h"
}

#define MAX_FMD_SIZE 2048
#define SCORE_THRESHOLD 21474  // FAR ~0.001%

// Reutiliza funcoes do uareu4500
extern "C" {

extern int init_library();
extern int search_devices(std::string &device_name);
extern int open_device(std::string device_name, DPFPDD_DEV &hReader);
extern int close_device(DPFPDD_DEV hReader);
extern int capture_fid(DPFPDD_DEV hReader, unsigned int &fid_size, unsigned char* &fid_data);
extern int transform_fid_to_fmd(unsigned int fid_size, unsigned char* fid_data,
                                 unsigned char* &fmd, unsigned int &fmd_size);
extern std::vector<unsigned char> convert_base64_to_fmd(const std::string& base64_data);

/*
 * python_identify_finger
 * Parametros:
 *   templates_b64 — array de strings base64 separadas por '\n'
 *   n             — numero de templates
 * Retorna indice do match (0-based) ou -1 se nao encontrou
 */
__declspec(dllexport)
int python_identify_finger(const char** templates_b64, int n) {
    if (n <= 0 || templates_b64 == nullptr) return -1;

    // Inicializa e abre leitor
    init_library();
    std::string device_name;
    search_devices(device_name);
    DPFPDD_DEV hReader = NULL;
    if (open_device(device_name, hReader) != 0) return -1;

    // Captura dedo atual
    unsigned int fid_size = 0;
    unsigned char* fid_data = nullptr;
    if (capture_fid(hReader, fid_size, fid_data) != 0) {
        close_device(hReader);
        return -1;
    }

    unsigned int fmd_size = MAX_FMD_SIZE;
    unsigned char* fmd_cap = new unsigned char[fmd_size];
    if (transform_fid_to_fmd(fid_size, fid_data, fmd_cap, fmd_size) != 0) {
        close_device(hReader);
        delete[] fmd_cap;
        return -1;
    }
    close_device(hReader);

    // Compara contra cada template
    DPFJ_FMD_FORMAT fmt = DPFJ_FMD_ANSI_378_2004;
    for (int i = 0; i < n; i++) {
        std::vector<unsigned char> fmd_ref = convert_base64_to_fmd(
            std::string(templates_b64[i]));

        unsigned int score = 0xFFFFFFFF;
        int ret = dpfj_compare(
            fmt, fmd_cap, fmd_size, 0,
            fmt, fmd_ref.data(), (unsigned int)fmd_ref.size(), 0,
            &score);

        std::cout << "idx=" << i << " ret=" << ret << " score=" << score << std::endl;

        if (ret == DPFJ_SUCCESS && score < SCORE_THRESHOLD) {
            delete[] fmd_cap;
            return i;
        }
    }

    delete[] fmd_cap;
    return -1;
}

} // extern "C"
