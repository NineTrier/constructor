(function () {
    function extractInner(tokenString) {
        if (tokenString == null) {
            return '';
        }
        var text = String(tokenString).trim();
        if (text.startsWith('{:') && text.endsWith(':}')) {
            return text.slice(2, -2).trim();
        }
        return text;
    }

    function toIntList(value) {
        if (Array.isArray(value)) {
            return value
                .map(function (item) { return Number(item); })
                .filter(function (item) { return Number.isFinite(item) && item > 0; });
        }
        var numberValue = Number(value);
        if (Number.isFinite(numberValue) && numberValue > 0) {
            return [numberValue];
        }
        return [];
    }

    function uniqueNumbers(values) {
        var seen = {};
        var result = [];
        values.forEach(function (value) {
            var numberValue = Number(value);
            if (!Number.isFinite(numberValue) || numberValue <= 0) {
                return;
            }
            var key = String(numberValue);
            if (seen[key]) {
                return;
            }
            seen[key] = true;
            result.push(numberValue);
        });
        return result;
    }

    function normaliseKey(value) {
        return String(value || '').trim().toLowerCase();
    }

    function isHumanStrictMode() {
        if (window.DOC_TOKEN_HUMAN_STRICT !== undefined) {
            return !!window.DOC_TOKEN_HUMAN_STRICT;
        }
        var meta = document.querySelector('meta[name="doc_token_human_strict"]');
        if (!meta) {
            return false;
        }
        var value = String(meta.getAttribute('content') || '').toLowerCase();
        return value === '1' || value === 'true' || value === 'yes';
    }

    function splitSelectorSuffix(segment) {
        var text = String(segment || '').trim();
        var match = /^(.*?)(?:\[(\*|\d+)\])?$/.exec(text);
        if (!match) {
            return {
                name: text,
                selector: 'first',
                index: null,
            };
        }
        var selectorRaw = match[2];
        var selector = 'first';
        var index = null;
        if (selectorRaw === '*') {
            selector = 'all';
        } else if (selectorRaw && /^\d+$/.test(selectorRaw)) {
            selector = 'index';
            index = Number(selectorRaw);
        }
        return {
            name: String(match[1] || '').trim(),
            selector: selector,
            index: index,
        };
    }

    function parseLegacyTokenString(tokenString) {
        var inner = extractInner(tokenString);
        if (!inner) {
            return null;
        }
        var parts = inner
            .split('.')
            .map(function (part) { return String(part || '').trim(); })
            .filter(Boolean);
        if (parts.length === 2) {
            return {
                version: 'legacy',
                depth: 0,
                objectName: parts[0],
                linkName: null,
                paramName: parts[1],
                steps: [
                    { kind: 'object_name', value: parts[0] },
                    { kind: 'param_name', value: parts[1] }
                ]
            };
        }
        if (parts.length === 3) {
            return {
                version: 'legacy',
                depth: 1,
                objectName: parts[0],
                linkName: parts[1],
                paramName: parts[2],
                steps: [
                    { kind: 'object_name', value: parts[0] },
                    { kind: 'link_name', value: parts[1] },
                    { kind: 'param_name', value: parts[2] }
                ]
            };
        }
        return null;
    }

    function parseCanonicalToken(tokenString) {
        var inner = extractInner(tokenString);
        if (!inner) {
            return null;
        }
        var objectMatch = /^obj\((\d+)\)/i.exec(inner);
        if (!objectMatch) {
            return null;
        }
        var objectId = Number(objectMatch[1]);
        var cursor = objectMatch[0].length;
        var linkSteps = [];
        var steps = [{ kind: 'object_id', value: objectId }];

        while (cursor < inner.length) {
            var next = inner.slice(cursor);
            var linkMatch = /^\.link\((\d+)\)(\[(\*|\d+)\])?/i.exec(next);
            if (linkMatch) {
                var selectorRaw = linkMatch[3] || '';
                var selectorKind = 'first';
                var selectorIndex = null;
                if (selectorRaw === '*') {
                    selectorKind = 'all';
                } else if (selectorRaw && /^\d+$/.test(selectorRaw)) {
                    selectorKind = 'index';
                    selectorIndex = Number(selectorRaw);
                }
                var linkStep = {
                    linkMetaId: Number(linkMatch[1]),
                    selector: selectorKind,
                    index: selectorIndex,
                };
                linkSteps.push(linkStep);
                steps.push({
                    kind: 'link_meta_id',
                    value: linkStep.linkMetaId,
                    selector: selectorKind,
                    index: selectorIndex,
                });
                cursor += linkMatch[0].length;
                continue;
            }

            var paramMatch = /^\.param\((\d+)\)$/i.exec(next);
            if (paramMatch) {
                var paramId = Number(paramMatch[1]);
                steps.push({ kind: 'param_id', value: paramId });
                return {
                    version: 'canonical',
                    depth: linkSteps.length,
                    objectId: objectId,
                    linkMetaId: linkSteps.length ? linkSteps[0].linkMetaId : null,
                    linkSteps: linkSteps,
                    paramId: paramId,
                    steps: steps
                };
            }
            return null;
        }
        return null;
    }

    function canonicalAstToToken(ast) {
        if (!ast || ast.version !== 'canonical' || !ast.objectId || !ast.paramId) {
            return null;
        }
        var linkSteps = Array.isArray(ast.linkSteps)
            ? ast.linkSteps
            : (ast.linkMetaId ? [{ linkMetaId: ast.linkMetaId, selector: 'first', index: null }] : []);
        var token = '{:obj(' + Number(ast.objectId) + ')';
        for (var idx = 0; idx < linkSteps.length; idx += 1) {
            var step = linkSteps[idx] || {};
            token += '.link(' + Number(step.linkMetaId || 0) + ')';
            if (step.selector === 'all') {
                token += '[*]';
            } else if (step.selector === 'index' && step.index != null) {
                token += '[' + Number(step.index) + ']';
            }
        }
        token += '.param(' + Number(ast.paramId) + '):}';
        return token;
    }

    function findObjectIdByName(nameIndex, objectName) {
        if (!nameIndex || !nameIndex.objects_by_name) {
            return null;
        }
        var direct = nameIndex.objects_by_name[objectName];
        if (direct !== undefined && direct !== null && direct !== '') {
            return Number(direct);
        }
        return null;
    }

    function findObjectIdsByName(nameIndex, objectName) {
        if (!nameIndex || !nameIndex.objects_by_name) {
            return [];
        }
        var map = nameIndex.objects_by_name || {};
        var direct = toIntList(map[objectName]);
        if (direct.length) {
            return uniqueNumbers(direct);
        }
        var needle = normaliseKey(objectName);
        var candidates = [];
        Object.keys(map).forEach(function (name) {
            if (normaliseKey(name) === needle) {
                candidates = candidates.concat(toIntList(map[name]));
            }
        });
        return uniqueNumbers(candidates);
    }

    function findObjectIdsByNameStrict(nameIndex, objectName) {
        if (!nameIndex || !nameIndex.objects_by_name) {
            return [];
        }
        var map = nameIndex.objects_by_name || {};
        return uniqueNumbers(toIntList(map[objectName]));
    }

    function findParamIdByName(nameIndex, objectId, paramName) {
        if (!nameIndex || !nameIndex.params_by_object_and_name) {
            return null;
        }
        var objectKey = String(objectId);
        var map = nameIndex.params_by_object_and_name[objectKey] || {};
        var value = map[paramName];
        if (value === undefined || value === null || value === '') {
            return null;
        }
        return Number(value);
    }

    function findParamIdsByName(nameIndex, objectId, paramName) {
        if (!nameIndex || !nameIndex.params_by_object_and_name) {
            return [];
        }
        var objectKey = String(objectId);
        var map = nameIndex.params_by_object_and_name[objectKey] || {};
        var direct = toIntList(map[paramName]);
        if (direct.length) {
            return uniqueNumbers(direct);
        }
        var needle = normaliseKey(paramName);
        var candidates = [];
        Object.keys(map).forEach(function (name) {
            if (normaliseKey(name) === needle) {
                candidates = candidates.concat(toIntList(map[name]));
            }
        });
        return uniqueNumbers(candidates);
    }

    function findParamIdsByNameStrict(nameIndex, objectId, paramName) {
        if (!nameIndex || !nameIndex.params_by_object_and_name) {
            return [];
        }
        var objectKey = String(objectId);
        var map = nameIndex.params_by_object_and_name[objectKey] || {};
        return uniqueNumbers(toIntList(map[paramName]));
    }

    function findLinkMetaIdByName(nameIndex, parentObjectId, linkName) {
        if (!nameIndex) {
            return null;
        }
        var parentKey = String(parentObjectId);
        var byDisplay = (nameIndex.links_meta_by_parent_and_display || {})[parentKey] || {};
        if (byDisplay[linkName] !== undefined && byDisplay[linkName] !== null && byDisplay[linkName] !== '') {
            return Number(byDisplay[linkName]);
        }
        var byChild = (nameIndex.links_meta_by_parent_and_child_object_name || {})[parentKey] || {};
        if (byChild[linkName] !== undefined && byChild[linkName] !== null && byChild[linkName] !== '') {
            return Number(byChild[linkName]);
        }
        return null;
    }

    function findLinkMetaIdsByName(nameIndex, parentObjectId, linkName) {
        if (!nameIndex) {
            return [];
        }
        var parentKey = String(parentObjectId);
        var byDisplay = (nameIndex.links_meta_by_parent_and_display || {})[parentKey] || {};
        var byChild = (nameIndex.links_meta_by_parent_and_child_object_name || {})[parentKey] || {};
        var byLinkParamName = (nameIndex.links_meta_by_parent_and_link_param_name || {})[parentKey] || {};
        var candidates = [];

        candidates = candidates.concat(toIntList(byDisplay[linkName]));
        candidates = candidates.concat(toIntList(byChild[linkName]));
        candidates = candidates.concat(toIntList(byLinkParamName[linkName]));

        if (!candidates.length) {
            var needle = normaliseKey(linkName);
            [byDisplay, byChild, byLinkParamName].forEach(function (lookupMap) {
                Object.keys(lookupMap || {}).forEach(function (name) {
                    if (normaliseKey(name) === needle) {
                        candidates = candidates.concat(toIntList(lookupMap[name]));
                    }
                });
            });
        }

        if (!candidates.length && nameIndex.links_meta_by_id) {
            var linksById = nameIndex.links_meta_by_id || {};
            var needleMeta = normaliseKey(linkName);
            Object.keys(linksById).forEach(function (metaId) {
                var meta = linksById[metaId] || {};
                if (String(meta.parent_object_id) !== String(parentObjectId)) {
                    return;
                }
                var displayMatch = normaliseKey(meta.display_name) === needleMeta;
                var linkParamMatch = normaliseKey(meta.link_parameter_name) === needleMeta;
                if (displayMatch || linkParamMatch) {
                    candidates = candidates.concat(toIntList(metaId));
                }
            });
        }

        return uniqueNumbers(candidates);
    }

    function findLinkMetaIdsByNameStrict(nameIndex, parentObjectId, linkName) {
        if (!nameIndex) {
            return [];
        }
        var parentKey = String(parentObjectId);
        var byDisplay = (nameIndex.links_meta_by_parent_and_display || {})[parentKey] || {};
        var byLinkParamName = (nameIndex.links_meta_by_parent_and_link_param_name || {})[parentKey] || {};
        var candidates = [];
        candidates = candidates.concat(toIntList(byDisplay[linkName]));
        candidates = candidates.concat(toIntList(byLinkParamName[linkName]));
        return uniqueNumbers(candidates);
    }

    function buildCanonicalAst(objectId, linkSteps, paramId) {
        var steps = [{ kind: 'object_id', value: Number(objectId) }];
        (linkSteps || []).forEach(function (step) {
            steps.push({
                kind: 'link_meta_id',
                value: Number(step.linkMetaId),
                selector: step.selector || 'first',
                index: step.index == null ? null : Number(step.index),
            });
        });
        steps.push({ kind: 'param_id', value: Number(paramId) });
        return {
            version: 'canonical',
            depth: (linkSteps || []).length,
            objectId: Number(objectId),
            linkMetaId: (linkSteps || []).length ? Number(linkSteps[0].linkMetaId) : null,
            linkSteps: (linkSteps || []).map(function (step) {
                return {
                    linkMetaId: Number(step.linkMetaId),
                    selector: step.selector || 'first',
                    index: step.index == null ? null : Number(step.index),
                };
            }),
            paramId: Number(paramId),
            steps: steps,
        };
    }

    function parseHumanTokenToAst(tokenString, nameIndex) {
        var strictMode = isHumanStrictMode();
        var inner = extractInner(tokenString);
        if (!inner) {
            return {
                ok: false,
                error: {
                    code: 'INVALID_TOKEN',
                    message: 'Пустой токен.',
                },
            };
        }

        var canonical = parseCanonicalToken(inner);
        if (canonical) {
            return {
                ok: true,
                ast: canonical,
                canonicalToken: canonicalAstToToken(canonical),
                source: 'canonical',
            };
        }

        var parts = inner
            .split('.')
            .map(function (part) { return String(part || '').trim(); })
            .filter(Boolean);
        if (parts.length < 2) {
            return {
                ok: false,
                error: {
                    code: 'INVALID_TOKEN',
                    message: 'Ожидается формат Объект.Параметр или Объект.Роль.Параметр.',
                },
            };
        }

        var objectCandidates = strictMode
            ? findObjectIdsByNameStrict(nameIndex, parts[0])
            : findObjectIdsByName(nameIndex, parts[0]);
        if (!objectCandidates.length) {
            return {
                ok: false,
                error: {
                    code: 'OBJECT_NOT_FOUND',
                    message: 'Объект из токена не найден.',
                    details: { object_name: parts[0] },
                },
            };
        }
        if (objectCandidates.length > 1) {
            return {
                ok: false,
                error: {
                    code: 'OBJECT_AMBIGUOUS',
                    message: 'Имя объекта неоднозначно.',
                    details: { object_name: parts[0], candidates: objectCandidates },
                },
            };
        }

        var currentObjectId = Number(objectCandidates[0]);
        var roleParts = parts.slice(1, -1);
        var paramPart = parts[parts.length - 1];
        var linkSteps = [];

        for (var idx = 0; idx < roleParts.length; idx += 1) {
            var parsedRole = splitSelectorSuffix(roleParts[idx]);
            var linkCandidates = strictMode
                ? findLinkMetaIdsByNameStrict(nameIndex, currentObjectId, parsedRole.name)
                : findLinkMetaIdsByName(nameIndex, currentObjectId, parsedRole.name);
            if (!linkCandidates.length) {
                return {
                    ok: false,
                    error: {
                        code: 'LINK_ROLE_NOT_FOUND',
                        message: 'Связь (роль) из токена не найдена.',
                        details: { parent_object_id: currentObjectId, role_name: parsedRole.name },
                    },
                };
            }
            if (linkCandidates.length > 1) {
                return {
                    ok: false,
                    error: {
                        code: 'LINK_ROLE_AMBIGUOUS',
                        message: 'Роль из токена неоднозначна.',
                        details: { parent_object_id: currentObjectId, role_name: parsedRole.name, candidates: linkCandidates },
                    },
                };
            }
            var linkMetaId = Number(linkCandidates[0]);
            var linkInfo = (nameIndex && nameIndex.links_meta_by_id ? nameIndex.links_meta_by_id : {})[String(linkMetaId)] || {};
            var childObjectId = Number(linkInfo.child_object_id || 0);
            if (!childObjectId) {
                return {
                    ok: false,
                    error: {
                        code: 'LINK_ROLE_NOT_FOUND',
                        message: 'Для роли не найден дочерний объект.',
                        details: { link_meta_id: linkMetaId },
                    },
                };
            }
            linkSteps.push({
                linkMetaId: linkMetaId,
                selector: parsedRole.selector || 'first',
                index: parsedRole.index == null ? null : Number(parsedRole.index),
            });
            currentObjectId = childObjectId;
        }

        var parsedParam = splitSelectorSuffix(paramPart);
        var paramCandidates = strictMode
            ? findParamIdsByNameStrict(nameIndex, currentObjectId, parsedParam.name)
            : findParamIdsByName(nameIndex, currentObjectId, parsedParam.name);
        if (!paramCandidates.length) {
            return {
                ok: false,
                error: {
                    code: 'PARAM_NOT_FOUND',
                    message: 'Параметр из токена не найден.',
                    details: { object_id: currentObjectId, param_name: parsedParam.name },
                },
            };
        }
        if (paramCandidates.length > 1) {
            return {
                ok: false,
                error: {
                    code: 'PARAM_AMBIGUOUS',
                    message: 'Имя параметра неоднозначно.',
                    details: { object_id: currentObjectId, param_name: parsedParam.name, candidates: paramCandidates },
                },
            };
        }

        var ast = buildCanonicalAst(objectCandidates[0], linkSteps, paramCandidates[0]);
        return {
            ok: true,
            ast: ast,
            canonicalToken: canonicalAstToToken(ast),
            source: 'human',
        };
    }

    function legacyAstToCanonicalToken(ast, nameIndex) {
        if (!ast || ast.version !== 'legacy') {
            return null;
        }
        var text = ast.linkName
            ? ast.objectName + '.' + ast.linkName + '.' + ast.paramName
            : ast.objectName + '.' + ast.paramName;
        var parsed = parseHumanTokenToAst('{:' + text + ':}', nameIndex);
        return parsed && parsed.ok ? parsed.canonicalToken : null;
    }

    function getTokenDisplayName(ast, nameIndex) {
        if (!ast) {
            return '';
        }
        if (ast.version === 'legacy') {
            if (ast.linkName) {
                return ast.objectName + '.' + ast.linkName + '.' + ast.paramName;
            }
            return ast.objectName + '.' + ast.paramName;
        }
        if (ast.version !== 'canonical') {
            return '';
        }
        var objectName = String(ast.objectId);
        var paramName = String(ast.paramId);
        var linkName = null;
        var objectByName = nameIndex && nameIndex.objects_by_name ? nameIndex.objects_by_name : {};
        Object.keys(objectByName).forEach(function (name) {
            if (Number(objectByName[name]) === Number(ast.objectId)) {
                objectName = name;
            }
        });

        var linkSteps = Array.isArray(ast.linkSteps)
            ? ast.linkSteps
            : (ast.linkMetaId ? [{ linkMetaId: ast.linkMetaId, selector: 'first', index: null }] : []);
        if (linkSteps.length > 0) {
            var linksById = nameIndex && nameIndex.links_meta_by_id ? nameIndex.links_meta_by_id : {};
            var paramsByObject = nameIndex && nameIndex.params_by_object_and_name ? nameIndex.params_by_object_and_name : {};
            var currentObjectId = Number(ast.objectId);
            var linkParts = [];
            for (var idx = 0; idx < linkSteps.length; idx += 1) {
                var step = linkSteps[idx];
                var linkInfo = linksById[String(step.linkMetaId)] || {};
                if (linkInfo.display_name) {
                    linkName = String(linkInfo.display_name);
                } else {
                    linkName = String(step.linkMetaId);
                }
                if (step.selector === 'all') {
                    linkName += '[*]';
                } else if (step.selector === 'index' && step.index != null) {
                    linkName += '[' + String(step.index) + ']';
                }
                linkParts.push(linkName);
                var childObjectId = Number(linkInfo.child_object_id || 0);
                if (childObjectId) {
                    currentObjectId = childObjectId;
                }
            }
            var childParams = paramsByObject[String(currentObjectId)] || {};
            Object.keys(childParams).forEach(function (name) {
                if (Number(childParams[name]) === Number(ast.paramId)) {
                    paramName = name;
                }
            });
            return [objectName].concat(linkParts).concat([paramName]).join('.');
        }

        var params = (nameIndex && nameIndex.params_by_object_and_name ? nameIndex.params_by_object_and_name : {})[String(ast.objectId)] || {};
        Object.keys(params).forEach(function (name) {
            if (Number(params[name]) === Number(ast.paramId)) {
                paramName = name;
            }
        });
        return objectName + '.' + paramName;
    }

    window.docTokenParser = {
        parseLegacyTokenString: parseLegacyTokenString,
        parseCanonicalToken: parseCanonicalToken,
        parseHumanTokenToAst: parseHumanTokenToAst,
        canonicalAstToToken: canonicalAstToToken,
        legacyAstToCanonicalToken: legacyAstToCanonicalToken,
        getTokenDisplayName: getTokenDisplayName,
        findObjectIdByName: findObjectIdByName,
        findParamIdByName: findParamIdByName,
        findLinkMetaIdByName: findLinkMetaIdByName,
    };
})();
