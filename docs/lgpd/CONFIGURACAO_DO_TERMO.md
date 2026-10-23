# Configuração dos termos de privacidade (guia para a academia)

Acesso: menu **Termos LGPD**, disponível só para usuários com perfil **admin**.

> O texto inicial é apenas um **modelo**. A academia deve preencher os dados reais e revisar o
> conteúdo, de preferência com um profissional jurídico ou um especialista em proteção de dados.
> O sistema registra quem foi informado e quem consentiu, mas **não garante** conformidade com a LGPD.

## Ciência e consentimento: dois tipos de termo

| Tipo | Botões | Quando usar |
|---|---|---|
| **Ciência** (ex.: Aviso de Privacidade) | "Li e estou ciente" | Para **informar** como os dados são tratados. Não é consentimento. Vale para tratamentos que a academia faz por outras bases legais, como contrato, obrigação legal ou legítimo interesse. |
| **Consentimento** | "Concordo" / "Não concordo" | Só para operações que **realmente dependem de consentimento**. A recusa é registrada e respeitada. |

A academia define quais operações dependem de consentimento e qual é a base legal de cada
tratamento. O sistema não presume nenhuma delas e vem apenas com o Aviso de Privacidade.

Ao cadastrar um termo de consentimento, defina o campo **"Bloqueia o sistema se recusado"**:
- **Sim:** quem recusar não usa o sistema e vê a tela com as consequências e o contato da academia.
- **Não:** a recusa bloqueia só as funções marcadas no código com `@requer_consentimento('<codigo>')`.
  Nesta versão nenhuma função está marcada, então isso exige uma alteração no software.

A recusa **nunca** exclui a conta nem os dados. Quem recusou pode rever a decisão em "Privacidade".

## Passo a passo

1. **Contato de privacidade:** em Termos LGPD → Configuração, preencha o canal que aparece para quem
   recusar um consentimento (nome do responsável, e-mail, telefone ou local de atendimento).
2. **Editar o aviso:** em Termos → Aviso de Privacidade → Versões → 1.0 (rascunho) → Editar.
   - Substitua todos os trechos `[PREENCHER ...]` pelos dados reais.
   - Remova o parágrafo "ATENÇÃO" do início.
   - O sistema **não publica** enquanto houver `[PREENCHER` no texto.
3. **Publicar:** escolha a vigência imediata ou agendada e confirme.
   - Depois de publicada, a versão **não pode ser alterada nem excluída**, nem pelo banco.
   - Para corrigir, crie uma nova versão.
4. **Ligar a exigência:** em Configuração, marque "Exigir resposta aos termos".
   - Só é possível ligar se existir um termo ativo com versão publicada.
   - A partir do próximo acesso, cada usuário verá a tela antes de usar o sistema.
5. **Acompanhar:**
   - **Situação** mostra quem está pendente, ciente, concordou ou recusou.
   - **Manifestações** mostra o histórico completo.
   - **Auditoria** mostra quem criou, editou ou publicou cada termo.

## Novas versões

- Crie a nova versão (é possível copiar a vigente), edite e publique.
- **Alteração material**:
  - **Sim**: todos precisam responder de novo. Use quando a mudança afeta finalidades, dados,
    compartilhamento, retenção ou direitos.
  - **Não**: o texto é atualizado sem pedir nova resposta. Use para correção de digitação ou
    formatação.
- A primeira versão de um termo é sempre material.
- O histórico das versões anteriores e das respostas dadas a elas é mantido.

## O que fica registrado em cada resposta

Usuário, termo e versão, resposta, data e hora (do servidor de banco), versão do software e o
**hash SHA-256 do texto exibido**. O hash permite provar exatamente qual conteúdo foi apresentado:
o texto da versão fica guardado e pode ser conferido em Versões → Ver.

Não são coletados IP, localização, assinatura nem outros dados.
